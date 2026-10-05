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


def generate_plan(session: Session, student: Student, *, start_term: str,
                  program_key_: str | None = None, catalog_year: str | None = None,
                  constraints: PlanningConstraints | None = None) -> PlanResult:
    if not is_term(start_term) or start_term[4] not in "0179":
        raise InvalidStartTerm(f"{start_term!r} is not a Rutgers term code (YYYYT, T in 0/1/7/9).")
    last = session.scalar(select(StudentCourse.term_code)
                          .where(StudentCourse.student_id == student.id)
                          .order_by(StudentCourse.term_code.desc()).limit(1))
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
    if session.new or session.dirty or session.deleted:
        session.rollback()
        raise ScenarioWouldWrite("plan generation attempted to modify state")
    return result


__all__ = ["InvalidStartTerm", "generate_plan"]
