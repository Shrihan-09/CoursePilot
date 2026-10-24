"""Generate a plan on demand (Phase 6.5). Read-only; nothing is persisted.

A plan is a pure function of (student record, program version, SOC data,
constraints, start term); its metadata carries fingerprints of each so a
later phase can tell it is stale. Persisting plans would add a cache to
invalidate without a consumer that needs it yet - see DATA_MODEL section 40.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.planning import PlanningConstraints, PlanResult
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

    result = PlanningEngine(session, constraints).plan(student, version, target, start_term,
                                                       assumptions)
    result.assumptions += pending_term_assumptions(session, start_term, result)
    if session.new or session.dirty or session.deleted:
        session.rollback()
        raise ScenarioWouldWrite("plan generation attempted to modify state")
    return result


__all__ = ["InvalidStartTerm", "generate_plan", "latest_history_term"]
