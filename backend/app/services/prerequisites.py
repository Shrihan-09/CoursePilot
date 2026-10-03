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
| an INTERPRETED minimum grade (Phase 6.4) | evaluated on the student's grades |
| an interpreted "OR PLACEMENT TEST / PERMISSION" alternative (6.4) | prerequisite OR UNKNOWN |
| any condition still NOT interpreted | caps SATISFIED at UNKNOWN |
| campuses publish different prerequisites for the term | UNKNOWN (`campus_prerequisites_differ`) |

Grades come from app.domain.grades; which attempt answers is
app.domain.attempts.prerequisite_grade - any attempt that earned the grade,
unlike the Degree Engine's E-credit rule.

Read-only, deterministic, and no language model is involved anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.attempts import Attempt
from app.domain.grades import Tri, earns_credit, outcome
from app.domain.prerequisites import (
    AnyOf,
    AttemptState,
    Evaluation,
    GradeCondition,
    course_keys,
    PrereqStatus,
    Unsupported,
    evaluate,
    from_json,
)
from app.models import Course, CourseOffering, CoursePrerequisite, Student, StudentCourse


@dataclass(slots=True)
class PrerequisiteCheck:
    course_key: str
    term_code: str
    status: PrereqStatus
    has_prerequisite: bool
    #: What Rutgers published, verbatim, and how CoursePilot classified it.
    raw_text: str | None = None
    condition_note: str | None = None
    section_condition_note: str | None = None
    classification: str | None = None
    canonical_text: str | None = None
    #: Phase 6.4: how CoursePilot read the condition texts (None: no condition).
    interpreted_conditions: dict | None = None
    #: The courses the published expression names (co-requisites compare
    #: against this to detect a "PRE OR COREQ" relaxation).
    expression_courses: list[str] = field(default_factory=list)
    evidence: Evaluation | None = None
    reasons: list[str] = field(default_factory=list)


def attempts_by_course(session: Session, student: Student) -> dict[str, list[Attempt]]:
    """Every attempt, with grade and credit origin, per course key. Read-only."""
    out: dict[str, list[Attempt]] = {}
    rows = session.execute(
        select(Course.course_string, StudentCourse.term_code, StudentCourse.status,
               StudentCourse.grade, StudentCourse.credit_origin)
        .join(Course, Course.id == StudentCourse.course_id)
        .where(StudentCourse.student_id == student.id)
        .order_by(Course.course_string, StudentCourse.term_code)
    ).all()
    for course_key, term, status, grade, origin in rows:
        out.setdefault(course_key, []).append(
            Attempt(course_key, term, status, grade, origin or "rutgers"))
    return out


def attempt_history(session: Session, student: Student) -> dict[str, AttemptState]:
    """Phase 6.2 view: best standing per course, passed > in progress > not passed.

    Kept for callers that only need completion. Grades are classified by
    app.domain.grades (Phase 6.4): an outcome whose credit is unknown (a
    temporary grade, NG) is IN_PROGRESS-like, never PASSED.
    """
    rank = {AttemptState.NOT_PASSED: 0, AttemptState.IN_PROGRESS: 1, AttemptState.PASSED: 2}
    history: dict[str, AttemptState] = {}
    for course_key, attempts in attempts_by_course(session, student).items():
        for a in attempts:
            credit = earns_credit(outcome(a.status, a.grade, a.credit_origin))
            state = (AttemptState.PASSED if a.status == "completed" and credit is Tri.YES
                     else AttemptState.IN_PROGRESS if a.status != "planned"
                     and credit is Tri.UNKNOWN
                     else AttemptState.NOT_PASSED)
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
    history = attempts_by_course(session, student)
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
    if len({(r.raw_text, r.condition_note, r.section_condition_note) for r in rows}) > 1:
        return PrerequisiteCheck(key, term_code, PrereqStatus.UNKNOWN, True,
                                 raw_text=rows[0].raw_text,
                                 reasons=["campus_prerequisites_differ"])

    row = rows[0]
    expr = (from_json(row.expression) if row.expression is not None
            else Unsupported(row.classification,
                             row.raw_text or row.condition_note or row.section_condition_note
                             or ""))
    interpreted = row.interpreted_conditions
    grade_condition = None
    if interpreted is not None:
        unmodeled = tuple(interpreted.get("uninterpreted", ()))
        minimum = interpreted.get("minimum_grade")
        if minimum:
            grade_condition = GradeCondition(minimum["grade"], minimum["scope"],
                                             frozenset(minimum.get("courses", ())))
        alternatives = sorted({k for alt in interpreted.get("alternatives", ())
                               for k in alt["kinds"]})
        if alternatives and row.expression is not None:
            # "PREREQ - X OR PLACEMENT TEST": the published prerequisite OR an
            # alternative CoursePilot cannot check.
            expr = AnyOf((expr, *(Unsupported(k, k) for k in alternatives)))
    else:
        # Rows loaded before Phase 6.4: the Phase 6.2 behaviour, unchanged.
        conditions = tuple((row.condition_kinds or "").split(",")) if row.condition_note else ()
        unmodeled = tuple(c for c in conditions if c)
    evidence = evaluate(expr, history, unmodeled_conditions=unmodeled,
                        grade_condition=grade_condition)
    return PrerequisiteCheck(
        key, term_code, evidence.status, True,
        raw_text=row.raw_text, condition_note=row.condition_note,
        section_condition_note=row.section_condition_note,
        classification=row.classification, canonical_text=row.canonical_text,
        interpreted_conditions=interpreted,
        expression_courses=(course_keys(from_json(row.expression))
                            if row.expression is not None else []),
        evidence=evidence, reasons=list(evidence.unknown_reasons),
    )


__all__ = ["PrerequisiteCheck", "attempt_history", "attempts_by_course", "check", "check_many"]
