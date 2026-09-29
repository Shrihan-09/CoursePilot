"""Prerequisite lookup and evaluation for a student (Phase 6.2).

The foundation a future Planning Engine will call to answer

    "Could this student take course X in term T?"

It is NOT the planner: it recommends nothing and ranks nothing. It reads the
term-scoped prerequisite SOC published for that course in that term and
evaluates it against the student's record with three-valued logic, returning
evidence rather than a bare boolean.

Answers are deliberately conservative:

| situation | status |
|---|---|
| no offering of the course in that term | UNKNOWN (`no_offering_in_term`) - no data, not "no prerequisite" |
| offering exists, SOC published no prerequisite | SATISFIED, `has_prerequisite=False` |
| prerequisite parsed | the three-valued evaluation |
| unsupported / malformed / unknown expression | UNKNOWN - never guessed |
| a `courseNotes` condition (e.g. minimum grade) | caps SATISFIED at UNKNOWN |
| campuses publish different prerequisites for the term | UNKNOWN (`campus_prerequisites_differ`) |

Read-only, deterministic, and no language model is involved anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.prerequisites import (
    AttemptState,
    Evaluation,
    PrereqStatus,
    Unsupported,
    evaluate,
    from_json,
)
from app.models import Course, CourseOffering, CoursePrerequisite, Student, StudentCourse

#: Mirrors the Degree Engine: these grades earn nothing.
FAILING_GRADES = frozenset({"F", "D-", "NC", "W"})


@dataclass(slots=True)
class PrerequisiteCheck:
    course_key: str
    term_code: str
    status: PrereqStatus
    has_prerequisite: bool
    #: What Rutgers published, verbatim, and how CoursePilot classified it.
    raw_text: str | None = None
    condition_note: str | None = None
    classification: str | None = None
    canonical_text: str | None = None
    evidence: Evaluation | None = None
    reasons: list[str] = field(default_factory=list)


def attempt_history(session: Session, student: Student) -> dict[str, AttemptState]:
    """The student's best standing per course: passed > in progress > not passed.

    Every attempt is read; none is modified. A retake that passed makes the
    course PASSED however many failed attempts preceded it.
    """
    rank = {AttemptState.NOT_PASSED: 0, AttemptState.IN_PROGRESS: 1, AttemptState.PASSED: 2}
    history: dict[str, AttemptState] = {}
    rows = session.execute(
        select(Course.course_string, StudentCourse.status, StudentCourse.grade)
        .join(Course, Course.id == StudentCourse.course_id)
        .where(StudentCourse.student_id == student.id)
    ).all()
    for course_key, status, grade in rows:
        if status == "completed" and grade not in FAILING_GRADES:
            state = AttemptState.PASSED
        elif status == "in_progress":
            state = AttemptState.IN_PROGRESS
        else:
            state = AttemptState.NOT_PASSED
        if rank[state] > rank[history.get(course_key, AttemptState.NOT_PASSED)]:
            history[course_key] = state
        history.setdefault(course_key, state)
    return history


def check_many(
    session: Session,
    student: Student,
    course_keys: list[str],
    term_code: str,
) -> dict[str, PrerequisiteCheck]:
    """Evaluate many candidate courses for one term in a fixed number of queries.

    Three queries whatever the candidate count - offerings, prerequisites,
    history - so a planner scoring hundreds of candidates is not N+1.
    """
    history = attempt_history(session, student)
    offered = {
        key for key, in session.execute(
            select(Course.course_string)
            .join(CourseOffering, CourseOffering.course_id == Course.id)
            .where(Course.course_string.in_(course_keys), CourseOffering.term_code == term_code)
        ).all()
    }
    published: dict[str, list[CoursePrerequisite]] = {}
    for key, prereq in session.execute(
        select(Course.course_string, CoursePrerequisite)
        .join(Course, Course.id == CoursePrerequisite.course_id)
        .where(Course.course_string.in_(course_keys), CoursePrerequisite.term_code == term_code)
    ).all():
        published.setdefault(key, []).append(prereq)

    return {key: _check(key, term_code, key in offered, published.get(key, []), history)
            for key in course_keys}


def check(session: Session, student: Student, course_key: str, term_code: str) -> PrerequisiteCheck:
    return check_many(session, student, [course_key], term_code)[course_key]


def _check(key, term_code, offered, rows, history) -> PrerequisiteCheck:
    if not offered:
        return PrerequisiteCheck(key, term_code, PrereqStatus.UNKNOWN, False,
                                 reasons=["no_offering_in_term"])
    if not rows:
        return PrerequisiteCheck(key, term_code, PrereqStatus.SATISFIED, False,
                                 reasons=["no_prerequisite_published"])
    if len({(r.raw_text, r.condition_note) for r in rows}) > 1:
        return PrerequisiteCheck(key, term_code, PrereqStatus.UNKNOWN, True,
                                 raw_text=rows[0].raw_text,
                                 reasons=["campus_prerequisites_differ"])

    row = rows[0]
    expr = (from_json(row.expression) if row.expression is not None
            else Unsupported(row.classification, row.raw_text or row.condition_note or ""))
    conditions = tuple((row.condition_kinds or "").split(",")) if row.condition_note else ()
    evidence = evaluate(expr, history, unmodeled_conditions=tuple(c for c in conditions if c))
    return PrerequisiteCheck(
        key, term_code, evidence.status, True,
        raw_text=row.raw_text, condition_note=row.condition_note,
        classification=row.classification, canonical_text=row.canonical_text,
        evidence=evidence, reasons=list(evidence.unknown_reasons),
    )


__all__ = ["FAILING_GRADES", "PrerequisiteCheck", "attempt_history", "check", "check_many"]
