"""Sanity checks over the student's RECORD (correctness fixes, item 1).

A record row whose term order is impossible - a course recorded BEFORE the
prerequisite it needs - is flagged for review, never corrected: the term may
be mis-read from a transcript, or the course may really have been taken out of
order (a waiver, a pre-matriculation course). CoursePilot cannot tell which,
so it says so and changes nothing.

"Impossible" is decided structurally and conservatively: the course's published
prerequisite expression is NOT met by the courses recorded strictly before it
(any status, grades ignored), and IS met once the courses recorded after it are
counted too. A condition CoursePilot cannot read (a placement test, "permission
of instructor") makes the answer unknown, and unknown is never flagged.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.prerequisites import (
    AllOf,
    AnyOf,
    AtLeast,
    ConcurrentReq,
    CourseReq,
    MinimumCourse,
    Unsupported,
    from_json,
)
from app.models import Course, CoursePrerequisite, Student, StudentCourse
from app.services.planning.terms import label


@dataclass(frozen=True)
class TermOrderConflict:
    course: str
    term_code: str
    #: Prerequisite courses the record has only in LATER terms: (course, term).
    later: tuple[tuple[str, str], ...]

    @property
    def message(self) -> str:
        names = ", ".join(f"{c} ({label(t)})" for c, t in self.later)
        return (f"NEEDS REVIEW - {self.course} is recorded in {label(self.term_code)}, before "
                f"its prerequisite {names}. Check that course's term on your transcript "
                "import; Coursenav has not changed your record.")


def _met(expr, have: set[str], same: set[str]) -> bool | None:
    """True / False / None (cannot tell) - course structure only, no grades."""
    if isinstance(expr, CourseReq):
        return expr.course_key in have
    if isinstance(expr, ConcurrentReq):
        return expr.course_key in have or expr.course_key in same
    if isinstance(expr, MinimumCourse):
        return any(expr.admits(k) for k in have)
    if isinstance(expr, Unsupported):
        return None
    values = [_met(c, have, same) for c in expr.children]
    if isinstance(expr, AllOf):
        return False if False in values else None if None in values else True
    if isinstance(expr, AnyOf):
        return True if True in values else None if None in values else False
    if isinstance(expr, AtLeast):
        yes = values.count(True)
        if yes >= expr.n:
            return True
        return None if yes + values.count(None) >= expr.n else False
    return None


def _courses(expr) -> set[str]:
    if isinstance(expr, CourseReq | ConcurrentReq | MinimumCourse):
        return {expr.course_key}
    if isinstance(expr, Unsupported):
        return set()
    return set().union(*(_courses(c) for c in expr.children)) if expr.children else set()


def term_order_conflicts(session: Session, student: Student) -> list[TermOrderConflict]:
    rows = session.execute(
        select(Course.course_string, StudentCourse.term_code, StudentCourse.credit_origin)
        .join(Course, Course.id == StudentCourse.course_id)
        .where(StudentCourse.student_id == student.id,
               StudentCourse.status.in_(("completed", "in_progress")))).all()
    if not rows:
        return []
    # Transfer and exam credit has no meaningful Rutgers term: always "before".
    always = {k for k, _, origin in rows if origin != "rutgers"}
    dated = sorted((t, k) for k, t, origin in rows if origin == "rutgers" and t)
    keys = sorted({k for _, k in dated})
    published: dict[str, dict[str, CoursePrerequisite]] = {}
    for key, row in session.execute(
            select(Course.course_string, CoursePrerequisite)
            .join(Course, Course.id == CoursePrerequisite.course_id)
            .where(Course.course_string.in_(keys), Course.supplement_code == "",
                   CoursePrerequisite.expression.is_not(None))):
        published.setdefault(key, {})[row.term_code] = row
    out = []
    everything = always | {k for _, k in dated}
    for term, key in dated:
        by_term = published.get(key)
        if not by_term:
            continue
        # The rules of the term it was taken, else the nearest published.
        rules = by_term.get(term) or by_term[min(by_term, key=lambda t: (abs(int(t) - int(term)),
                                                                          t))]
        expr = from_json(rules.expression)
        lists = (rules.interpreted_conditions or {}).get("prerequisite_lists", ())
        if lists:
            expr = AllOf((expr, *(AnyOf(tuple(CourseReq(k) for k in codes)) for codes in lists)))
        before = always | {k for t, k in dated if t < term}
        same = {k for t, k in dated if t == term}
        if _met(expr, before, same) is not False:
            continue
        if _met(expr, everything - {key}, same) is not True:
            continue
        later = tuple(sorted(((k, t) for t, k in dated
                              if t > term and k in _courses(expr) and k not in before),
                             key=lambda x: (x[1], x[0])))
        if later:
            out.append(TermOrderConflict(course=key, term_code=term, later=later))
    return out


__all__ = ["TermOrderConflict", "term_order_conflicts"]
