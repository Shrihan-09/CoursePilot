"""Generate a plan on demand (Phase 6.5). Read-only; nothing is persisted.

A plan is a pure function of (student record, program version, SOC data,
constraints, start term); its metadata carries fingerprints of each so a
later phase can tell it is stale. Persisting plans would add a cache to
invalidate without a consumer that needs it yet - see DATA_MODEL section 40.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.planning import (
    PlanningConstraints,
    PlanningIssue,
    PlanResult,
    SchoolCoreStatus,
    Severity,
)
from app.domain.scenario import ScenarioAssumption
from app.models import Program, ProgramVersion, School, Student, StudentCourse
from app.services.planning.engine import PlanningEngine
from app.services.planning.terms import is_term
from app.services.programs import program_key
from app.services.scenarios import ScenarioError, ScenarioWouldWrite, _assumptions, resolve_target


class InvalidStartTerm(ScenarioError):
    pass


def _current_key(session: Session, student: Student) -> str:
    program, school = session.execute(
        select(Program, School)
        .join(ProgramVersion, ProgramVersion.program_id == Program.id)
        .join(School, School.id == Program.school_id)
        .where(ProgramVersion.id == student.program_version_id)).one()
    return program_key(school.code, program.code, program.degree_type, program.variant)


def latest_history_term(session: Session, student: Student) -> str | None:
    """The latest term of COMPLETED or IN-PROGRESS coursework.

    Phase 7.2.1: planned rows no longer count. A course the student only
    intends to take is not history - counting it pushed the plan's start past
    terms the student has not taken (persona audit, finding N3)."""
    return session.scalar(select(StudentCourse.term_code)
                          .where(StudentCourse.student_id == student.id,
                                 StudentCourse.status.in_(("completed", "in_progress")))
                          .order_by(StudentCourse.term_code.desc()).limit(1))


def pending_term_assumptions(session: Session, start_term: str,
                             result: PlanResult) -> list[ScenarioAssumption]:
    """Semesters in the plan's span that Rutgers publishes but CoursePilot has
    not validly loaded: nothing in them was placed on history (7.2.4)."""
    from app.services.planning.terms import label
    from app.services.term_authority import TermAuthority, term_states

    last = max((t.term_code for t in result.terms), default=start_term)
    return [ScenarioAssumption(
        code="target_term_data_pending",
        message=(f"Rutgers has published {label(code)}, but CoursePilot hasn't loaded and "
                 f"checked that schedule yet, so no course was placed in {label(code)} on "
                 "past offerings. Regenerate once it is loaded."))
        for code, state in sorted(term_states(session).items())
        if state.authority is TermAuthority.PUBLISHED_PENDING and start_term <= code <= last]


def degree_navigator_overlay(session: Session, student: Student, version: ProgramVersion,
                             key: str) -> tuple:
    """(overlay for this program and its school Core, plan notes) - Phase 7.4.

    No confirmed DN audit: CoursePilot's own audit decides, labelled
    "estimated". With one: DN's status decides mapped requirements, and every
    disagreement with CoursePilot's own check is stated as NEEDS REVIEW."""
    from app.services.academic_context import school_requirement_sets
    from app.services.degree_audit.overlay import DISAGREE_LABEL, ESTIMATED_LABEL, active_overlay

    keys = {key} | {s.code for s in school_requirement_sets(session, version)}
    overlay = active_overlay(session, student).for_keys(keys)
    if not overlay:
        return None, [ScenarioAssumption(
            code="requirement_status_estimated",
            message=f"Requirement status: {ESTIMATED_LABEL}.")]
    dates = sorted({s.as_of.date().isoformat() for s in overlay.statuses.values() if s.as_of})
    notes = [ScenarioAssumption(
        code="requirement_status_from_degree_navigator",
        message=(f"Requirement status for {len(overlay.statuses)} requirement(s) comes from your "
                 f"Degree Navigator audit (as of {', '.join(dates) or 'unknown date'}); "
                 "CoursePilot's own audit is the cross-check. Unmapped requirements are "
                 "estimated by CoursePilot."))]
    for status in sorted(overlay.statuses.values(), key=lambda s: s.requirement_code):
        if status.disagrees:
            notes.append(ScenarioAssumption(
                code="degree_navigator_disagreement",
                message=(f"NEEDS REVIEW - {status.name or status.requirement_code}: "
                         f"{DISAGREE_LABEL}. Planning uses Degree Navigator's status: "
                         f"{status.rule}")))
    return overlay, notes


def requirement_parents(session: Session, version: ProgramVersion) -> dict[str, str | None]:
    """code -> parent code over the program and its school Core."""
    from app.services.academic_context import composed_requirements

    reqs = composed_requirements(session, version)
    by_id = {r.id: r.code for r in reqs}
    return {r.code: by_id.get(r.parent_id) for r in reqs}


def annotate_recommendations(result: PlanResult, overlay, parents: dict[str, str | None]) -> None:
    """Phase 7.4 (decision 5): every recommendation says when the requirement it
    counts toward - or a group containing it - takes its status from the
    student's Degree Navigator audit, and says NEEDS REVIEW where DN and
    CoursePilot disagree."""
    from app.services.degree_audit.overlay import DISAGREE_LABEL

    for term in result.terms:
        for course in term.courses:
            seen: set[str] = set()
            for contribution in course.requirements:
                code: str | None = contribution.requirement_code
                while code is not None and code not in seen:
                    seen.add(code)
                    status = overlay.statuses.get(code)
                    if status is not None:
                        name = status.name or code
                        if status.disagrees:
                            note = (f"NEEDS REVIEW - {name}: {DISAGREE_LABEL}. Planning uses "
                                    f"Degree Navigator's status: {status.rule}")
                        else:
                            note = (f"{name}: status from your Degree Navigator audit - "
                                    f"{status.rule}")
                        if note not in course.notes:
                            course.notes.append(note)
                    code = parents.get(code)


def school_core_status(session: Session, version: ProgramVersion) -> SchoolCoreStatus:
    """Which school Core applies - chosen by the PROGRAM's school (Phase 7.6).

    A school whose Core CoursePilot has not encoded (SEBS, SOE, ...) has
    status "unknown": the plan says so and never substitutes SAS Core."""
    from app.services.academic_context import school_requirement_sets

    school = session.get(School, version.program.school_id)
    sets = school_requirement_sets(session, version)
    if sets:
        names = ", ".join(s.name for s in sets)
        return SchoolCoreStatus(
            school_code=school.code, status="encoded", requirement_sets=[s.code for s in sets],
            message=f"Core requirements: {names}, chosen by your program's school ({school.code}).")
    return SchoolCoreStatus(
        school_code=school.code, status="unknown",
        message=(f"Core status is unknown: CoursePilot has not encoded the "
                 f"{school.name or school.code} Core requirements yet, so this plan covers only "
                 "your program's own requirements. SAS Core is not used in their place."))


def declaration_of(session: Session, student: Student, version: ProgramVersion, key: str):
    """Phase 7.6: the 'planning toward X (not yet declared)' block, or None."""
    from app.services.declaration import declaration_for

    program = version.program
    return declaration_for(session, student, program_key=key, program_name=program.name,
                           degree_type=program.degree_type, variant=program.variant or "",
                           catalog_year=version.catalog_year)


#: The words Prompt 05 asks for, for a DN requirement CoursePilot cannot map.
UNMAPPED_OPEN = "Degree Navigator says this is still open, and CoursePilot can't map it yet."


def unmapped_degree_navigator_issues(session: Session, student: Student,
                                     version: ProgramVersion, key: str) -> list[PlanningIssue]:
    """Phase 7.6: every requirement a confirmed DN audit shows NOT satisfied (or
    only in progress/planned) that has no CoursePilot mapping. Shown, never
    filled with a guess - no course is planned for it."""
    from app.models.degree_audit import DegreeAuditImport, DegreeAuditRequirementRow
    from app.services.academic_context import school_requirement_sets

    keys = {key} | {s.code for s in school_requirement_sets(session, version)}
    out = []
    for imp, row in session.execute(
            select(DegreeAuditImport, DegreeAuditRequirementRow)
            .join(DegreeAuditRequirementRow,
                  DegreeAuditRequirementRow.import_id == DegreeAuditImport.id)
            .where(DegreeAuditImport.student_id == student.id,
                   DegreeAuditImport.state == "confirmed",
                   DegreeAuditImport.superseded_at.is_(None),
                   DegreeAuditRequirementRow.excluded.is_(False),
                   # Requirement headings only: a Core GOAL row (AHp, AHq...)
                   # belongs to its requirement, which decides; an unfilled
                   # goal of a fulfilled requirement is not "still open".
                   DegreeAuditRequirementRow.kind == "requirement",
                   DegreeAuditRequirementRow.requirement_code.is_(None),
                   DegreeAuditRequirementRow.dn_status.in_(("not_satisfied",
                                                            "in_progress_or_planned")))
            .order_by(DegreeAuditImport.confirmed_at, DegreeAuditRequirementRow.row_index)):
        if imp.mapping_key is not None and imp.mapping_key not in keys:
            continue                     # another program's audit (a what-if elsewhere)
        name = row.dn_heading or row.extracted_title or row.dn_code or f"row {row.row_index}"
        out.append(PlanningIssue(
            severity=Severity.NEEDS_CONFIRMATION, code="DN_REQUIREMENT_OPEN_UNMAPPED",
            message=f"{name}: {UNMAPPED_OPEN}",
            details={"dn_heading": row.dn_heading, "dn_code": row.dn_code,
                     "dn_status": row.dn_status, "needs": row.extracted_needs,
                     "kind": row.kind}))
    return out


def generate_plan(session: Session, student: Student, *, start_term: str,
                  program_key_: str | None = None, catalog_year: str | None = None,
                  constraints: PlanningConstraints | None = None) -> PlanResult:
    if not is_term(start_term) or start_term[4] not in "0179":
        raise InvalidStartTerm(f"{start_term!r} is not a Rutgers term code (YYYYT, T in 0/1/7/9).")
    last = latest_history_term(session, student)
    if last is not None and start_term <= last:
        raise InvalidStartTerm(
            f"The plan must start after your latest recorded term ({last}); "
            "recorded and in-progress courses are already part of the plan's input.")

    key = program_key_ or _current_key(session, student)
    version, target = resolve_target(session, student, key, catalog_year)
    assumptions = [ScenarioAssumption(
        code="plan_is_a_projection",
        message=("Planned courses count only provisionally: no grade is assumed, offerings "
                 "beyond loaded schedules are historical evidence, and load limits are "
                 "CoursePilot defaults, not Rutgers policy."))]
    if not target.is_current_program:
        assumptions += _assumptions(session, student, target)

    overlay, dn_notes = degree_navigator_overlay(session, student, version, key)
    assumptions += dn_notes
    core = school_core_status(session, version)
    if core.status == "unknown":
        assumptions.append(ScenarioAssumption(code="school_core_unknown", message=core.message))
    declaration = declaration_of(session, student, version, key)
    if declaration is not None:
        # The declaration block states what declaring takes (or that no
        # official source does); the generic "not evaluated" note would
        # contradict it.
        assumptions = [a for a in assumptions if a.code != "admission_not_modeled"]
        assumptions.insert(0, ScenarioAssumption(
            code="planning_toward_undeclared_major",
            message=(f"{declaration.label}. Sections restricted to declared majors are treated "
                     "as closed to you; minor and honors restrictions stay unconfirmed.")))
    result = PlanningEngine(session, constraints, overlay).plan(student, version, target,
                                                                start_term, assumptions)
    result.assumptions += pending_term_assumptions(session, start_term, result)
    result.school_core = core
    result.declaration = declaration
    if core.status == "unknown":
        result.issues.append(PlanningIssue(
            severity=Severity.WARNING, code="SCHOOL_CORE_UNKNOWN", message=core.message,
            details={"school": core.school_code}))
    result.issues += unmapped_degree_navigator_issues(session, student, version, key)
    if overlay:
        annotate_recommendations(result, overlay, requirement_parents(session, version))
    if session.new or session.dirty or session.deleted:
        session.rollback()
        raise ScenarioWouldWrite("plan generation attempted to modify state")
    return result


__all__ = ["UNMAPPED_OPEN", "InvalidStartTerm", "annotate_recommendations",
           "declaration_of", "degree_navigator_overlay", "generate_plan", "latest_history_term",
           "requirement_parents", "school_core_status", "unmapped_degree_navigator_issues"]
