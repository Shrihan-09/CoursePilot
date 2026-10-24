"""What each course-change intent would do to a saved plan (Phase 7.2.4).

Read-only analysis: every option is computed from the plan's own evidence,
the requirement's official course pool and target-term authority. It never
edits a plan, never picks a replacement for the student, and never claims a
replacement is eligible - the planner verifies that when a change is applied.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.plan_change import ChangeIntent, ChangeOption, ChangeOptions, ReplacementCandidate
from app.domain.planning import OfferingEvidenceKind, PlanResult
from app.models import (
    Course,
    CourseOffering,
    CourseSection,
    RequirementCourseOption,
    Student,
    StudentCourse,
)
from app.services.planning.offerings import PLACEABLE, OfferingIndex
from app.services.term_authority import TermAuthority, authority_of, term_states


class CourseNotInPlan(LookupError):
    pass


def _pool(session: Session, student: Student, codes: list[str]) -> dict[str, list[str]]:
    from app.models import ProgramVersion
    from app.services.academic_context import composed_requirements

    version = session.get(ProgramVersion, student.program_version_id)
    ids = {r.id: r.code for r in composed_requirements(session, version) if r.code in codes}
    pool: dict[str, list[str]] = {code: [] for code in codes}
    for req_id, course in session.execute(
            select(RequirementCourseOption.requirement_id, Course.course_string)
            .join(Course, Course.id == RequirementCourseOption.course_id)
            .where(RequirementCourseOption.requirement_id.in_(list(ids)))
            .order_by(Course.course_string)).all():
        if course not in pool[ids[req_id]]:
            pool[ids[req_id]].append(course)
    return pool


def change_options(session: Session, student: Student, plan: PlanResult,
                   course: str, term_code: str) -> ChangeOptions:
    planned = {(pc.course, t.term_code): pc for t in plan.terms for pc in t.courses}
    pc = planned.get((course, term_code))
    if pc is None:
        raise CourseNotInPlan(f"{course} is not planned in {term_code}")
    requirements = sorted({r.requirement_code for r in pc.requirements})
    in_plan = {c for c, _ in planned}
    term_of = {c: t for c, t in planned}
    dependents = sorted({other.course for other in planned.values()
                         if any(d.course == course for d in other.prerequisite.conditional_on)})
    before = max((d.term_code for d in pc.prerequisite.conditional_on if d.source == "planned"),
                 default=None)
    after = min((term_of[d] for d in dependents), default=None)
    taken = set(session.scalars(
        select(Course.course_string).join(StudentCourse, StudentCourse.course_id == Course.id)
        .where(StudentCourse.student_id == student.id,
               StudentCourse.status.in_(("completed", "in_progress")))))
    pool = _pool(session, student, requirements)
    index = OfferingIndex(session, {course} | {c for cs in pool.values() for c in cs})
    authority = authority_of(term_states(session), term_code).authority
    sections = session.scalar(
        select(func.count()).select_from(CourseSection)
        .join(CourseOffering, CourseOffering.id == CourseSection.offering_id)
        .join(Course, Course.id == CourseOffering.course_id)
        .where(Course.course_string == course, CourseSection.term_code == term_code)) or 0
    open_codes = [f"requirement_left_open:{c}" for c in requirements]
    broken = [f"loses_prerequisite_for:{d}" for d in dependents]

    replacements = [
        ReplacementCandidate(course=alt, requirement_code=code,
                             offering_status=index.evidence(alt, term_code).status.value)
        for code in requirements for alt in pool[code]
        if alt != course and alt not in in_plan and alt not in taken
        and index.evidence(alt, term_code).kind in PLACEABLE]
    moves = [t.term_code for t in plan.terms
             if t.term_code != term_code
             and (before is None or t.term_code > before)
             and (after is None or t.term_code < after)
             and index.evidence(course, t.term_code).kind in PLACEABLE]
    section_data = (authority is TermAuthority.READY
                    and pc.offering.kind is OfferingEvidenceKind.CONFIRMED_IN_TERM and sections > 0)
    later_requirements = sorted({r.requirement_code for t in plan.terms if t.term_code > term_code
                                 for o in t.courses for r in o.requirements} - set(requirements))
    same_term = [o for (c, t), o in planned.items() if t == term_code]

    options = [
        ChangeOption(intent=ChangeIntent.REPLACE_SAME_REQUIREMENT,
                     available=bool(replacements),
                     reason="alternatives_in_requirement_pool" if replacements
                     else ("no_requirement" if not requirements else "no_placeable_alternative"),
                     replacements=replacements, consequences=broken),
        ChangeOption(intent=ChangeIntent.MOVE_TO_ANOTHER_TERM, available=bool(moves),
                     reason="terms_respecting_prerequisites_and_offerings" if moves
                     else "no_term_respects_prerequisites_and_offerings",
                     candidates=moves),
        ChangeOption(intent=ChangeIntent.REMOVE_AND_LEAVE_REQUIREMENT_OPEN, available=True,
                     reason="always_possible", consequences=open_codes + broken),
        ChangeOption(intent=ChangeIntent.FIND_DIFFERENT_SECTION,
                     available=section_data and sections > 1,
                     reason=(f"sections_in_term:{sections}" if section_data
                             else "no_section_data_for_term"), candidates=[term_code]),
        ChangeOption(intent=ChangeIntent.CHANGE_MODALITY, available=section_data,
                     reason=("section_data_available" if section_data
                             else "no_section_data_for_term"),
                     candidates=[term_code]),
        ChangeOption(intent=ChangeIntent.REDUCE_SEMESTER_LOAD, available=len(same_term) > 1,
                     reason=("term_has_other_courses" if len(same_term) > 1
                             else "only_course_in_term"),
                     consequences=["moves_to_a_later_term"] + broken),
        ChangeOption(intent=ChangeIntent.PRIORITIZE_DIFFERENT_REQUIREMENT,
                     available=bool(later_requirements),
                     reason="later_requirements_exist" if later_requirements
                     else "no_other_requirement_later",
                     candidates=later_requirements, consequences=open_codes),
    ]
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("change analysis must not modify state")
    return ChangeOptions(course=course, term_code=term_code, requirements=requirements,
                         dependents=dependents, options=options)


__all__ = ["CourseNotInPlan", "change_options"]
