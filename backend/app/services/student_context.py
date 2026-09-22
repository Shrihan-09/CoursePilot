"""The authenticated student's academic context (Phase 5.6).

## Five layers, and why they must not blur

```
OIDC JWT          -> identity          who is asking          (app/api/auth.py)
UserAccount       -> ownership         which record is theirs (app/services/accounts.py)
Student + rows    -> academic FACTS    what is recorded       (this module)
Degree Engine     -> INTERPRETATION    what it means          (app/services/audit/)
Catalog / search  -> description       what a course is       (app/services/search/)
```

This module reads **facts**. It reports what the database records and stops
there. It computes no satisfaction, no remaining credits, no progress, no
eligibility - every one of those is an *interpretation*, and interpretation
belongs to the Degree Engine, which is the sole authority for academic
correctness.

The temptation this module exists to resist: a credit total is one `sum()`
away, and it would be wrong. Not arithmetically - the sum of
`credits_earned` is a fact. Wrong because a client rendering "38 credits"
next to a degree requirement is reading it as *credits toward the degree*,
and that number is smaller: program rules exclude some courses
(`credits_excluded` in the audit), and only the engine knows which. A
plausible number in the wrong place is worse than no number.

So `StudentContext` carries per-course credits and no totals. Totals come
from the audit, where they carry the engine's meaning.

## Status is preserved exactly

`completed`, `in_progress` and `planned` are three different things and stay
three different lists. The engine treats them differently - completed
satisfies, in-progress satisfies only provisionally, planned satisfies
nothing (Phase 4.5 baseline semantics) - and flattening them here would let
a client re-invent a rule the engine already owns.

## One row in, one row out

The query joins `Course` on `course_id` only. Joining `CourseOffering` or
`CourseSection` would fan a single record row into one per term or per
section, and a duplicated course is a fabricated course.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Course,
    EnrollmentStatus,
    Program,
    ProgramVersion,
    School,
    Student,
    StudentCourse,
)


@dataclass(frozen=True, slots=True)
class CourseRecord:
    """One course on the record, as recorded. Nothing derived."""

    course_string: str
    supplement_code: str
    title: str | None
    term_code: str
    #: Always None for in-progress and planned work - the database CHECK
    #: constraint only requires a grade for completed courses.
    grade: str | None
    #: What the record says was earned, which may legitimately differ from the
    #: catalog credits (variable-credit courses, transfer credit).
    credits_earned: Decimal | None
    #: What the catalog says the course is worth. Both are carried because
    #: they answer different questions and silently preferring one would hide
    #: a discrepancy a student may need to query.
    catalog_credits: Decimal | None
    #: `student_self_reported` until a registrar feed exists. Surfaced because
    #: a student is entitled to know that CoursePilot is reasoning from
    #: evidence they supplied, not from an official record.
    source_kind: str


@dataclass(frozen=True, slots=True)
class ProgramContext:
    program_name: str
    program_code: str
    degree_type: str
    school_code: str | None
    school_name: str | None
    #: The catalog year the STUDENT is bound to.
    catalog_year: str
    #: The catalog year of the program version on record. Normally identical;
    #: reported separately because a mismatch is a real academic problem the
    #: engine raises as a blocking finding, and hiding it behind one field
    #: would make the API disagree with the audit.
    program_version_catalog_year: str
    total_credits_min: Decimal | None
    total_credits_max: Decimal | None
    #: `unverified` until a human has checked the curation against published
    #: prose. A student reading their requirements deserves to know this.
    curation_status: str
    source_url: str | None


@dataclass(frozen=True, slots=True)
class StudentContext:
    """Facts only. No totals, no progress, no satisfaction."""

    program: ProgramContext
    completed: list[CourseRecord] = field(default_factory=list)
    in_progress: list[CourseRecord] = field(default_factory=list)
    planned: list[CourseRecord] = field(default_factory=list)


def _record(sc: StudentCourse, course: Course) -> CourseRecord:
    return CourseRecord(
        course_string=course.course_string,
        supplement_code=course.supplement_code,
        title=course.title,
        term_code=sc.term_code,
        grade=sc.grade,
        credits_earned=sc.credits_earned,
        catalog_credits=course.credits,
        source_kind=sc.source_kind,
    )


class ProgramVersionMissing(Exception):
    """The student's program version row is absent.

    Not a 404 for the caller: the student record exists and is theirs. It is
    a server-side data problem, and reporting it as "not found" would tell a
    student their own record is missing.
    """


def build_student_context(session: Session, student: Student) -> StudentContext:
    """Assemble the authenticated student's academic context.

    Takes an already-resolved `Student`. It does **not** look one up, and
    takes no identifier of its own, so there is no argument a request could
    reach that would change whose record this reads. Ownership was settled
    before this function was called.
    """
    version = session.get(ProgramVersion, student.program_version_id)
    if version is None:
        raise ProgramVersionMissing(
            f"student {student.id} references a missing program version"
        )
    program = session.get(Program, version.program_id)
    school = session.get(School, program.school_id) if program else None

    program_context = ProgramContext(
        program_name=program.name if program else "",
        program_code=program.code if program else "",
        degree_type=program.degree_type if program else "",
        school_code=school.code if school else None,
        school_name=school.name if school else None,
        catalog_year=student.catalog_year,
        program_version_catalog_year=version.catalog_year,
        total_credits_min=version.total_credits_min,
        total_credits_max=version.total_credits_max,
        curation_status=version.curation_status,
        source_url=version.source_url,
    )

    # Deterministic ordering, matching the engine's, so two callers - and two
    # runs - see the same record in the same order. Ordering on a UUID would
    # be stable within a database and meaningless across a re-ingest.
    rows = session.execute(
        select(StudentCourse, Course)
        .join(Course, Course.id == StudentCourse.course_id)
        .where(StudentCourse.student_id == student.id)
        .order_by(
            Course.course_string,
            Course.supplement_code,
            StudentCourse.term_code,
        )
    ).all()

    buckets: dict[str, list[CourseRecord]] = {
        EnrollmentStatus.COMPLETED.value: [],
        EnrollmentStatus.IN_PROGRESS.value: [],
        EnrollmentStatus.PLANNED.value: [],
    }
    for sc, course in rows:
        # A status the database CHECK permits but this code does not know is
        # dropped rather than guessed into a bucket. Silently filing an
        # unknown status under "completed" would fabricate academic fact.
        bucket = buckets.get(sc.status)
        if bucket is not None:
            bucket.append(_record(sc, course))

    return StudentContext(
        program=program_context,
        completed=buckets[EnrollmentStatus.COMPLETED.value],
        in_progress=buckets[EnrollmentStatus.IN_PROGRESS.value],
        planned=buckets[EnrollmentStatus.PLANNED.value],
    )


__all__ = [
    "CourseRecord",
    "ProgramContext",
    "ProgramVersionMissing",
    "StudentContext",
    "build_student_context",
]
