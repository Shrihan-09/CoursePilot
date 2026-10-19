"""My current schedule (Phase 7.2.1; DATA_MODEL section 49.6) - DESCRIPTIVE, never generated.

The record says WHICH courses a student is taking now (in-progress rows). It
does not say which SECTIONS:

* a Rutgers unofficial transcript shows at most a section NUMBER (kept only
  in import staging - a hint, parsed from text);
* manual entry has no section field;
* a course can have dozens of sections in a term.

So CoursePilot never infers a section. It lists the real Rutgers sections of
each in-progress course for that term and the student CONFIRMS theirs. Only
confirmed sections are drawn as "My current schedule".

A confirmation stores the exact section (term + index + section row). If that
section is later missing from the term's data - removed by Rutgers, or the
term no longer loaded - it is reported as no longer published and never
retargeted to another section (the section-watch precedent, services/
monitoring.py).

The transcript's section number, when an import recorded one, may MARK the
matching section as "matches your transcript". It is never auto-confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.schedule import SectionChoice
from app.models import Course, Student, StudentCourse
from app.models.current_section import StudentCurrentSection
from app.models.onboarding import TranscriptImportRow
from app.services.scheduling.candidates import load_term, student_attributes
from app.services.scheduling.engine import _choice


class CurrentScheduleError(Exception):
    pass


class NotInProgress(CurrentScheduleError):
    """The course is not one the student is taking in the current term."""


class SectionNotAvailable(CurrentScheduleError):
    """No section with that index exists for that course in that term's data."""


@dataclass
class CurrentCourse:
    course: str
    title: str | None
    term_code: str
    transcript_section: str | None
    confirmed: list[SectionChoice] = field(default_factory=list)
    #: Confirmations whose section is no longer in the term's data.
    unavailable: list[dict] = field(default_factory=list)
    sections_available: int = 0
    confirmed_identities: list[dict] = field(default_factory=list)


@dataclass
class CurrentSchedule:
    term_code: str | None
    #: Several terms of in-progress courses: shown, never silently merged.
    other_in_progress_terms: list[str]
    term_published: bool
    courses: list[CurrentCourse]


def _in_progress(session: Session, student: Student):
    return session.execute(
        select(StudentCourse, Course)
        .join(Course, Course.id == StudentCourse.course_id)
        .where(StudentCourse.student_id == student.id, StudentCourse.status == "in_progress")
        .order_by(StudentCourse.term_code, Course.course_string)
    ).all()


def _hint(session: Session, student_course_id) -> str | None:
    value = session.scalar(
        select(TranscriptImportRow.extracted_class_section)
        .where(TranscriptImportRow.student_course_id == student_course_id)
        .order_by(TranscriptImportRow.created_at.desc(), TranscriptImportRow.id)
        .limit(1)
    )
    return value or None


def _candidates(session: Session, student: Student, term: str, courses: list[str]):
    data = load_term(session, term, courses, student_attributes(session, student))
    by_course: dict[str, list] = {}
    for cands in data.candidates.values():
        for c in cands:
            by_course.setdefault(c.course, []).append(c)
    return data, by_course


def current_schedule(session: Session, student: Student) -> CurrentSchedule:
    rows = _in_progress(session, student)
    if not rows:
        return CurrentSchedule(
            term_code=None, other_in_progress_terms=[], term_published=False, courses=[]
        )
    terms = sorted({sc.term_code for sc, _ in rows})
    term = terms[-1]
    current = [(sc, c) for sc, c in rows if sc.term_code == term]
    keys = sorted({c.course_string for _, c in current})
    data, by_course = _candidates(session, student, term, keys)
    confirmations = {}
    for row in session.scalars(
        select(StudentCurrentSection).where(
            StudentCurrentSection.student_id == student.id, StudentCurrentSection.term_code == term
        )
    ):
        confirmations.setdefault(row.student_course_id, []).append(row)
    out = []
    for sc, course in current:
        cands = by_course.get(course.course_string, [])
        item = CurrentCourse(
            course=course.course_string,
            title=course.title,
            term_code=term,
            transcript_section=_hint(session, sc.id),
            sections_available=len({c.index_number for c in cands}),
        )
        for row in sorted(
            confirmations.get(sc.id, []), key=lambda r: (r.component, r.supplement_code)
        ):
            item.confirmed_identities.append(
                {
                    "term_code": row.term_code,
                    "course_string": row.course_string,
                    "section_number": row.section_number,
                    "index_number": row.index_number,
                    "supplement_code": row.supplement_code,
                    "source": row.source,
                    "confirmed_at": row.confirmed_at,
                }
            )
            match = next(
                (
                    c
                    for c in cands
                    if c.index_number == row.index_number
                    and row.section_id is not None
                    and c.section_id == row.section_id
                    and (row.section_number is None or c.section_number == row.section_number)
                    and c.supplement_code == row.supplement_code
                ),
                None,
            )
            if match is None:
                item.unavailable.append(
                    {"index_number": row.index_number, "component": row.component}
                )
            else:
                item.confirmed.append(_choice(match, []))
        out.append(item)
    return CurrentSchedule(
        term_code=term,
        other_in_progress_terms=terms[:-1],
        term_published=data.published,
        courses=out,
    )


def _current_row(session: Session, student: Student, course: str):
    rows = _in_progress(session, student)
    if not rows:
        raise NotInProgress("No courses are in progress on your record.")
    term = max(sc.term_code for sc, _ in rows)
    for sc, c in rows:
        if sc.term_code == term and c.course_string == course:
            return sc, c, term
    raise NotInProgress(f"{course} is not in progress on your record this term.")


def sections_for(session: Session, student: Student, course: str):
    """The Rutgers sections of an in-progress course, for the student to choose from."""
    sc, _, term = _current_row(session, student, course)
    data, by_course = _candidates(session, student, term, [course])
    cands = sorted(
        by_course.get(course, []), key=lambda c: (c.component != "primary", c.index_number)
    )
    return term, data.published, [_choice(c, []) for c in cands], _hint(session, sc.id)


def confirm_section(
    session: Session, student: Student, course: str, index_number: str, now: datetime
) -> StudentCurrentSection:
    sc, _, term = _current_row(session, student, course)
    _, by_course = _candidates(session, student, term, [course])
    match = next((c for c in by_course.get(course, []) if c.index_number == index_number), None)
    if match is None:
        raise SectionNotAvailable(
            f"Index {index_number} is not a {course} section in this term's Rutgers data."
        )
    row = session.scalar(
        select(StudentCurrentSection).where(
            StudentCurrentSection.student_course_id == sc.id,
            StudentCurrentSection.component == match.component,
            StudentCurrentSection.supplement_code == match.supplement_code,
        )
    )
    if row is None:
        row = StudentCurrentSection(
            student_id=student.id, student_course_id=sc.id, component=match.component
        )
        session.add(row)
    row.term_code = term
    row.course_string = course
    row.section_number = match.section_number
    row.supplement_code = match.supplement_code
    row.index_number = match.index_number
    row.section_id = match.section_id
    row.source = "student_confirmed"
    row.confirmed_at = now
    session.flush()
    return row


def remove_confirmations(session: Session, student: Student, course: str) -> int:
    sc, _, _ = _current_row(session, student, course)
    rows = list(
        session.scalars(
            select(StudentCurrentSection).where(StudentCurrentSection.student_course_id == sc.id)
        )
    )
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)


__all__ = [
    "CurrentCourse",
    "CurrentSchedule",
    "CurrentScheduleError",
    "NotInProgress",
    "SectionNotAvailable",
    "confirm_section",
    "current_schedule",
    "remove_confirmations",
    "sections_for",
]
