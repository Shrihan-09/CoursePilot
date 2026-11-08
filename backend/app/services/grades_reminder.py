"""The "Your grades are in" Home reminder (Prompt 10a follow-up).

After a term's FINAL-grades date (the published Registrar dates in
`app.core.grade_calendar` - never estimated), a student whose latest confirmed
transcript or Degree Navigator import predates that date is asked to upload an
updated one, so the plan stays accurate. Dismissing it hides it until the NEXT
term's grades date.

A kind the student has never imported is included only when their stated first
semester is no later than the graded term (a first-year whose first grades are
not in yet is not nagged). With neither an import nor a stated first semester
there is nothing to anchor the reminder on, so it is not shown.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.grade_calendar import FINAL_GRADES, latest_final_grades
from app.models import DegreeAuditImport, Student, TranscriptImport
from app.services.onboarding import ProfileMissing, lock_student, owned_student

KINDS = ("transcript", "degree_navigator")


@dataclass(frozen=True, slots=True)
class GradesReminder:
    show: bool
    term_code: str | None = None
    grades_date: date | None = None
    source_url: str | None = None
    #: Which uploads are older than the grades date: transcript, degree_navigator.
    kinds: list[str] = field(default_factory=list)


def _latest_confirmed(session: Session, model, student: Student) -> datetime | None:
    return session.scalar(select(func.max(model.confirmed_at)).where(
        model.student_id == student.id, model.state == "confirmed"))


def decide(now: datetime, *, dismissed_term: str | None, first_term: str | None,
           confirmed: dict[str, datetime | None]) -> GradesReminder:
    """The rule, pure: `confirmed` is each kind's latest confirmed import time."""
    latest = latest_final_grades(now)
    if latest is None:
        return GradesReminder(show=False)
    term, at = latest
    day, url = FINAL_GRADES[term]
    if dismissed_term == term:
        return GradesReminder(show=False, term_code=term, grades_date=day, source_url=url)
    kinds = []
    for kind in KINDS:
        when = confirmed.get(kind)
        if when is not None:
            if when < at:
                kinds.append(kind)
        elif first_term is not None and first_term <= term:
            kinds.append(kind)
    return GradesReminder(show=bool(kinds), term_code=term, grades_date=day, source_url=url,
                          kinds=kinds)


def reminder_for(session: Session, student: Student, now: datetime) -> GradesReminder:
    confirmed = {kind: _latest_confirmed(session, model, student)
                 for kind, model in zip(KINDS, (TranscriptImport, DegreeAuditImport),
                                        strict=True)}
    return decide(now, dismissed_term=student.grades_reminder_dismissed_term,
                  first_term=student.first_term, confirmed=confirmed)


def grades_reminder(session: Session, account_id: uuid.UUID, now: datetime) -> GradesReminder:
    student = owned_student(session, account_id)
    if student is None:
        return GradesReminder(show=False)
    return reminder_for(session, student, now)


def dismiss_grades_reminder(session: Session, account_id: uuid.UUID,
                            now: datetime) -> GradesReminder:
    """Hide the reminder until the next term's final-grades date."""
    student = lock_student(session, account_id)
    if student is None:
        raise ProfileMissing("No academic record exists for this account.")
    latest = latest_final_grades(now)
    if latest is not None:
        student.grades_reminder_dismissed_term = latest[0]
        session.flush()
    return reminder_for(session, student, now)


__all__ = ["GradesReminder", "decide", "dismiss_grades_reminder", "grades_reminder",
           "reminder_for"]
