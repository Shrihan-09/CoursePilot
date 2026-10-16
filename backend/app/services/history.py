"""Self-service academic history: the student's own `student_course` rows (Phase 7.0).

Adds, edits and removes ONE entry at a time, for the authenticated owner
only. Validation is `app.domain.history.validate_entry` - the same function
transcript confirmation uses - and academic meaning (grades, retakes,
prerequisites, the audit, GPA) stays in the domain layer and the engines.
Nothing here computes any of it.

## Identity of an entry

`student_course` is keyed (student, course, term): the same course in two
terms is a retake and is TWO entries, both kept - the SAS repeat policy in
`app.domain.attempts` decides what each one counts for. The same course
twice in one term is refused (`DUPLICATE_ENTRY`); it is not a retake.

## Course identity

A course code resolves to ONE catalog course: the base record (supplement
"") when there is one, so the audit (which groups by course id) and the
prerequisite engine (which groups by course string) agree on what was taken.
A code CoursePilot has no course for is refused, never created - catalog
courses come only from Rutgers' Schedule of Classes.

## Concurrency

Every write takes the owner's student row lock first
(`app.services.onboarding.lock_student`), the same lock transcript
confirmation takes, so manual edits and a confirmation serialize. The
UNIQUE (student, course, term) constraint remains the final guard.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.observability import redact_id
from app.domain.history import Entry, Issue, IssueSeverity, validate_entry
from app.domain.provenance import SourceKind
from app.models import Course, Student, StudentCourse
from app.services.onboarding import ProfileMissing, invalidate_audit, lock_student

logger = logging.getLogger(__name__)

COURSE_CODE = re.compile(r"^\d{2}:\d{3}:\d{3}$")
#: A record bigger than this is not a student's record.
MAX_ENTRIES = 400


class HistoryError(Exception):
    code = "history_error"


class EntryInvalid(HistoryError):
    """Structured refusal: `issues` is what a client renders."""

    code = "entry_invalid"

    def __init__(self, issues: list[Issue]) -> None:
        super().__init__("The entry cannot be recorded as it is.")
        self.issues = issues


class EntryNotFound(HistoryError):
    code = "not_found"


@dataclass(frozen=True, slots=True)
class CourseMatch:
    course: Course | None
    issue: Issue | None


def resolve_course(session: Session, code: str | None, supplement: str | None = None,
                   *, unknown_severity: IssueSeverity = IssueSeverity.INVALID) -> CourseMatch:
    """A course code -> one catalog course, or an issue. Never creates one."""
    text = (code or "").strip()
    if not text:
        return CourseMatch(None, Issue("COURSE_CODE_MISSING", "course_code", unknown_severity,
                                       "Which course? Use its Rutgers code, e.g. 01:198:111."))
    if not COURSE_CODE.match(text):
        return CourseMatch(None, Issue("COURSE_CODE_INVALID", "course_code",
                                       IssueSeverity.INVALID,
                                       "A course code is school:subject:number, e.g. "
                                       "01:198:111."))
    rows = list(session.scalars(select(Course).where(Course.course_string == text)))
    if supplement is not None:
        wanted = supplement.strip().upper()
        rows = [c for c in rows if (c.supplement_code or "") == wanted]
    if not rows:
        return CourseMatch(None, Issue(
            "COURSE_NOT_FOUND", "course_code", unknown_severity,
            "CoursePilot has no course with this code. It may be retired, renumbered or "
            "not offered in the terms CoursePilot has loaded."))
    base = [c for c in rows if (c.supplement_code or "") == ""]
    if base:
        return CourseMatch(base[0], None)
    if len(rows) == 1:
        return CourseMatch(rows[0], None)
    return CourseMatch(None, Issue(
        "COURSE_AMBIGUOUS", "supplement_code", IssueSeverity.NEEDS_REVIEW,
        "This code has several registration records; say which one (supplement code)."))


def _require_valid(entry: Entry, extra: list[Issue] = ()) -> None:
    issues = [*extra, *entry.issues]
    if any(i.blocking for i in issues):
        raise EntryInvalid(issues)


def _duplicate(session: Session, student_id, course_id, term_code,
               exclude_id=None) -> StudentCourse | None:
    q = select(StudentCourse).where(StudentCourse.student_id == student_id,
                                    StudentCourse.course_id == course_id,
                                    StudentCourse.term_code == term_code)
    if exclude_id is not None:
        q = q.where(StudentCourse.id != exclude_id)
    return session.scalar(q)


def _duplicate_issue() -> Issue:
    return Issue("DUPLICATE_ENTRY", "term_code", IssueSeverity.INVALID,
                 "This course is already recorded for this term. A retake is the same "
                 "course in a different term.")


def list_entries(session: Session, student: Student) -> list[tuple[StudentCourse, Course]]:
    return list(session.execute(
        select(StudentCourse, Course).join(Course, Course.id == StudentCourse.course_id)
        .where(StudentCourse.student_id == student.id)
        .order_by(StudentCourse.term_code, Course.course_string, Course.supplement_code)).all())


def _locked(session: Session, account_id: uuid.UUID) -> Student:
    student = lock_student(session, account_id)
    if student is None:
        raise ProfileMissing("No academic record exists for this account.")
    return student


def add_entry(session: Session, account_id: uuid.UUID, *, course_code: str,
              supplement_code: str | None, term_code: str | None, status: str | None,
              grade: str | None, credits_earned, credit_origin: str | None) -> StudentCourse:
    student = _locked(session, account_id)
    entry = validate_entry(term_code=term_code, status=status, grade=grade,
                           credits_earned=credits_earned, credit_origin=credit_origin)
    match = resolve_course(session, course_code, supplement_code)
    _require_valid(entry, [match.issue] if match.issue else [])
    if _duplicate(session, student.id, match.course.id, entry.term_code) is not None:
        raise EntryInvalid([_duplicate_issue()])
    count = session.scalar(select(StudentCourse.id).where(
        StudentCourse.student_id == student.id).offset(MAX_ENTRIES - 1).limit(1))
    if count is not None:
        raise EntryInvalid([Issue("TOO_MANY_ENTRIES", None, IssueSeverity.INVALID,
                                  f"A record holds at most {MAX_ENTRIES} entries.")])
    row = StudentCourse(student_id=student.id, course_id=match.course.id,
                        term_code=entry.term_code, status=entry.status, grade=entry.grade,
                        credits_earned=entry.credits_earned,
                        credit_origin=entry.credit_origin,
                        source_kind=SourceKind.STUDENT_SELF_REPORTED.value)
    session.add(row)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        # Only reachable if something wrote this (course, term) without the
        # student lock; the constraint is the last word.
        raise EntryInvalid([_duplicate_issue()]) from None
    invalidate_audit(session, student.id)
    logger.info("history_entry_added", extra={"account": redact_id(account_id)})
    return row


#: The fields an edit may change. Course identity is not one: a different
#: course is a different entry (remove + add), never a silent re-pointing.
EDITABLE = ("term_code", "status", "grade", "credits_earned", "credit_origin")


def update_entry(session: Session, account_id: uuid.UUID, entry_id: uuid.UUID,
                 changes: dict) -> StudentCourse:
    """Apply a partial edit, then validate the WHOLE resulting entry."""
    student = _locked(session, account_id)
    row = session.scalar(select(StudentCourse).where(StudentCourse.id == entry_id,
                                                     StudentCourse.student_id == student.id))
    if row is None:
        raise EntryNotFound("No such entry.")
    merged = {name: getattr(row, name) for name in EDITABLE}
    merged.update({k: v for k, v in changes.items() if k in EDITABLE})
    entry = validate_entry(**merged)
    _require_valid(entry)
    if _duplicate(session, student.id, row.course_id, entry.term_code,
                  exclude_id=row.id) is not None:
        raise EntryInvalid([_duplicate_issue()])
    row.term_code, row.status, row.grade = entry.term_code, entry.status, entry.grade
    row.credits_earned, row.credit_origin = entry.credits_earned, entry.credit_origin
    # A corrected entry is the student's statement now, whatever its origin.
    row.source_kind = SourceKind.STUDENT_SELF_REPORTED.value
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        raise EntryInvalid([_duplicate_issue()]) from None
    invalidate_audit(session, student.id)
    logger.info("history_entry_updated", extra={"account": redact_id(account_id)})
    return row


def delete_entry(session: Session, account_id: uuid.UUID, entry_id: uuid.UUID) -> bool:
    """Remove one of the owner's entries. False if there is no such entry
    (already removed, or never theirs - indistinguishable on purpose)."""
    student = _locked(session, account_id)
    row = session.scalar(select(StudentCourse).where(StudentCourse.id == entry_id,
                                                     StudentCourse.student_id == student.id))
    if row is None:
        return False
    session.delete(row)
    session.flush()
    invalidate_audit(session, student.id)
    logger.info("history_entry_removed", extra={"account": redact_id(account_id)})
    return True


__all__ = ["COURSE_CODE", "EDITABLE", "MAX_ENTRIES", "CourseMatch", "EntryInvalid",
           "EntryNotFound", "HistoryError", "add_entry", "delete_entry", "list_entries",
           "resolve_course", "update_entry"]
