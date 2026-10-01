"""Hypothetical program scenarios over a student's real record (Phase 6.0).

> What does my current academic record look like under another program?

## Not a second engine

There is no `ScenarioDegreeEngine`. A scenario is `DegreeAuditEngine.audit`
with an explicit `program_version`, so every allocation, category, sharing,
exclusion and baseline rule is the one the real audit uses. A scenario can
therefore never disagree with the real audit about what a rule MEANS - only
about WHICH rules apply, which is the question being asked.

## Read-only

A scenario reads `Student` and `StudentCourse` and writes nothing:

  * `Student.program_version_id` is never touched - the hypothetical program
    is a function argument, not a stored fact;
  * no cached audit is read or written for the target (see below);
  * after evaluating, the session is checked for pending changes and the
    scenario refuses to return if any exist. The engine only selects, so this
    cannot fire today; it exists so that a future edit which makes evaluation
    write something fails loudly instead of quietly changing a student's major.

## Why scenarios bypass the audit cache

`student_audit_cache` holds ONE row per student, keyed by that student's
academic fingerprint - which includes their enrolled `program_version_id` -
plus `rules_token`, which is a single database-wide counter (`v:<n>`), not a
per-version value. A scenario computed through `audit_with_cache` would
therefore get exactly the ACTUAL audit's key, and its result would be stored
and later served as the student's real audit. `test_multi_program.py` proves
the keys coincide. Scenarios call the engine directly; measured on the
development database a scenario audit costs tens of milliseconds, so no
scenario cache is built until a measurement says one is needed.

## Catalog years are never guessed

With no explicit catalog year the target version must match the student's
own catalog year. If that version does not exist the request fails and names
the years that do - it never falls back to the latest. An explicit different
year is evaluated, and the result says Rutgers' rules for which catalog year
applies after a change of program are not modeled.

## What a scenario does not claim

It evaluates requirements. It does not claim the student could declare the
program: admission criteria, school transfer rules and departmental approval
are not modeled, and every non-current scenario says so.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.audit import (
    DegreeAuditResult,
    RequirementResult,
    RequirementStatus,
)
from app.domain.scenario import (
    CourseComparison,
    ProgramComparison,
    ProgramOutcome,
    RequirementOutcome,
    RequirementRef,
    ScenarioAssumption,
    ScenarioAudit,
    ScenarioTarget,
    SharedRequirementOutcome,
)
from app.models import Program, ProgramVersion, School, Student
from app.services.audit.engine import DegreeAuditEngine
from app.services.programs import (
    SupportStatus,
    find_program,
    program_key,
    version_info,
)


class ScenarioError(Exception):
    """The requested scenario cannot be evaluated. Message is client-safe."""


class ProgramNotFound(ScenarioError):
    pass


class CatalogYearUnavailable(ScenarioError):
    def __init__(self, message: str, available: list[str]) -> None:
        super().__init__(message)
        self.available = available


class ProgramNotEvaluable(ScenarioError):
    pass


class ScenarioWouldWrite(RuntimeError):
    """Evaluation left pending changes in the session. Never expected."""


# --------------------------------------------------------------------------
# target resolution
# --------------------------------------------------------------------------


def resolve_target(
    session: Session, student: Student, key: str, catalog_year: str | None
) -> tuple[ProgramVersion, ScenarioTarget]:
    found = find_program(session, key)
    if found is None:
        raise ProgramNotFound(f"No program {key!r} is known to CoursePilot.")
    program, school = found

    available = sorted((v.catalog_year for v in program.versions), reverse=True)
    wanted = catalog_year or student.catalog_year
    version = next((v for v in program.versions if v.catalog_year == wanted), None)
    if version is None:
        # Never the latest, never the closest: choosing a catalog year is an
        # academic decision and there is no rule here to make it with.
        raise CatalogYearUnavailable(
            f"{program.name} ({program.degree_type}) has no {wanted} catalog "
            f"version in CoursePilot. Available: {', '.join(available) or 'none'}.",
            available,
        )

    info = version_info(session, version)
    if not info.support_status.evaluable:
        raise ProgramNotEvaluable(
            f"{program.name} ({program.degree_type}) {wanted} has no curated "
            "requirements CoursePilot can evaluate."
        )

    return version, ScenarioTarget(
        program_key=program_key(school.code, program.code, program.degree_type,
                                program.variant),
        program_name=program.name,
        program_code=program.code,
        degree_type=program.degree_type,
        school_code=school.code,
        catalog_year=version.catalog_year,
        support_status=info.support_status.value,
        curation_status=version.curation_status,
        is_current_program=version.id == student.program_version_id,
    )


def _assumptions(
    session: Session, student: Student, target: ScenarioTarget
) -> list[ScenarioAssumption]:
    out = [
        ScenarioAssumption(
            code="hypothetical",
            message=(
                "This evaluates your recorded courses under another program's "
                "rules. It does not change your declared program."
            ),
        )
    ]
    if target.is_current_program:
        return out

    current_school = session.scalar(
        select(School.code)
        .join(Program, Program.school_id == School.id)
        .join(ProgramVersion, ProgramVersion.program_id == Program.id)
        .where(ProgramVersion.id == student.program_version_id)
    )
    out.append(
        ScenarioAssumption(
            code="admission_not_modeled",
            message=(
                "Whether you could declare this program - admission criteria, "
                "grade thresholds or departmental approval - is not evaluated."
            ),
        )
    )
    if current_school is not None and current_school != target.school_code:
        out.append(
            ScenarioAssumption(
                code="different_school",
                message=(
                    f"This program belongs to {target.school_code}, not your current "
                    f"school ({current_school}). School transfer rules are not modeled."
                ),
            )
        )
    if target.catalog_year != student.catalog_year:
        out.append(
            ScenarioAssumption(
                code="catalog_year_differs",
                message=(
                    f"Evaluated under the {target.catalog_year} catalog; your record "
                    f"is bound to {student.catalog_year}. Which catalog year would "
                    "apply after a change of program is not modeled."
                ),
            )
        )
    if target.support_status == SupportStatus.PENDING_REVIEW.value:
        out.append(
            ScenarioAssumption(
                code="requirements_pending_review",
                message=(
                    "Some of this program's requirements have not yet been verified "
                    "by a person against the official Rutgers prose."
                ),
            )
        )
    return out


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------


def run_scenario_audit(
    session: Session, student: Student, key: str, catalog_year: str | None = None
) -> ScenarioAudit:
    version, target = resolve_target(session, student, key, catalog_year)

    # The real engine, uncached - see the module docstring.
    result = DegreeAuditEngine(session).audit(student, program_version=version)

    if session.new or session.dirty or session.deleted:
        session.rollback()
        raise ScenarioWouldWrite("scenario evaluation attempted to modify state")

    return ScenarioAudit(
        target=target,
        assumptions=_assumptions(session, student, target),
        audit=result,
    )


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


def _walk(nodes: list[RequirementResult]):
    for node in nodes:
        yield node
        yield from _walk(node.children)


def _outcome(result: DegreeAuditResult) -> ProgramOutcome:
    leaves = [n for n in _walk(result.requirements) if not n.children]
    return ProgramOutcome(
        program_name=result.program_name,
        degree_type=result.degree_type,
        catalog_year=result.catalog_year,
        status=result.status,
        credits_applicable_to_degree=result.credits_applicable_to_degree,
        credits_excluded=result.credits_excluded,
        credits_required_min=result.credits_required_min,
        credits_remaining=result.credits_remaining,
        leaf_requirements=len(leaves),
        leaf_satisfied=sum(n.status is RequirementStatus.SATISFIED for n in leaves),
        leaf_provisionally_satisfied=sum(
            n.status is RequirementStatus.PROVISIONALLY_SATISFIED for n in leaves
        ),
    )


def _applications(result: DegreeAuditResult) -> dict[str, list[RequirementRef]]:
    applied: dict[str, list[RequirementRef]] = {}
    for allocation in result.allocation:
        refs = applied.setdefault(allocation.course.course_id, [])
        ref = RequirementRef(
            code=allocation.requirement_code,
            name=allocation.requirement_name,
            system=allocation.requirement_system,
        )
        if ref not in refs:
            refs.append(ref)
    return applied


def compare_audits(
    current: DegreeAuditResult, target: DegreeAuditResult
) -> ProgramComparison:
    """Read the difference off two audits. Pure: no session, no model."""
    cur_applied = _applications(current)
    tgt_applied = _applications(target)
    cur_excluded = {c.course_id for c in current.excluded_courses}
    tgt_excluded = {c.course_id for c in target.excluded_courses}

    refs = {}
    for result in (current, target):
        for c in (
            *(a.course for a in result.allocation),
            *result.excluded_courses,
            *result.unallocated_courses,
        ):
            refs.setdefault(c.course_id, c)

    buckets: dict[str, list[CourseComparison]] = {
        "both": [], "current": [], "target": [], "neither": [],
    }
    for course_id, ref in sorted(refs.items(), key=lambda kv: kv[1].course_string):
        row = CourseComparison(
            course_string=ref.course_string,
            title=ref.title,
            current_requirements=cur_applied.get(course_id, []),
            target_requirements=tgt_applied.get(course_id, []),
            excluded_by_current=course_id in cur_excluded,
            excluded_by_target=course_id in tgt_excluded,
        )
        in_cur, in_tgt = bool(row.current_requirements), bool(row.target_requirements)
        buckets["both" if in_cur and in_tgt else "current" if in_cur
                else "target" if in_tgt else "neither"].append(row)

    # A requirement is "the same" in both programs only when its code AND the
    # official prose it was curated from are identical - i.e. it was loaded
    # from the same source into both versions (SAS Core is the real case).
    # Code alone is not enough: codes are unique only WITHIN a version, and
    # the real CS and Mathematics definitions both contain a `MATH_151`
    # derived from different catalog pages.
    def identity(node: RequirementResult) -> tuple[str, str]:
        return (node.requirement_code, node.source_prose or "")

    cur_nodes = {identity(n): n for n in _walk(current.requirements)}
    tgt_nodes = {identity(n): n for n in _walk(target.requirements)}
    shared = sorted(cur_nodes.keys() & tgt_nodes.keys())

    return ProgramComparison(
        current=_outcome(current),
        target=_outcome(target),
        courses_applied_in_both=buckets["both"],
        courses_applied_only_current=buckets["current"],
        courses_applied_only_target=buckets["target"],
        courses_applied_in_neither=buckets["neither"],
        requirements_in_both=[
            SharedRequirementOutcome(
                code=key[0],
                name=cur_nodes[key].requirement_name,
                current_status=cur_nodes[key].status,
                target_status=tgt_nodes[key].status,
            )
            for key in shared
        ],
        requirements_only_current=[
            RequirementOutcome(code=n.requirement_code, name=n.requirement_name,
                               status=n.status)
            for key, n in cur_nodes.items() if key not in tgt_nodes
        ],
        requirements_only_target=[
            RequirementOutcome(code=n.requirement_code, name=n.requirement_name,
                               status=n.status)
            for key, n in tgt_nodes.items() if key not in cur_nodes
        ],
        notes=[
            "Both results come from the same Degree Engine over the same record.",
            "A requirement counts as shared only when its code and its source "
            "prose are identical in both programs.",
            "Courses are compared by course, not term; a retake appears once.",
            "No time-to-degree estimate is made: that depends on future courses.",
        ],
    )


def compare_with_current(
    session: Session, student: Student, key: str, catalog_year: str | None = None
) -> tuple[ScenarioAudit, DegreeAuditResult, ProgramComparison]:
    """Actual audit (cached, keyed on the real binding) vs the scenario."""
    from app.services.audit.cached_audit import audit_with_cache

    scenario = run_scenario_audit(session, student, key, catalog_year)
    actual, _ = audit_with_cache(session, student)
    return scenario, actual, compare_audits(actual, scenario.audit)


__all__ = [
    "CatalogYearUnavailable",
    "ProgramNotEvaluable",
    "ProgramNotFound",
    "ScenarioError",
    "ScenarioWouldWrite",
    "compare_audits",
    "compare_with_current",
    "resolve_target",
    "run_scenario_audit",
]
