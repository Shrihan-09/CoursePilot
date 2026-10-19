"""Read and confirm a student's Planning Profile (Phase 7.2.1; DATA_MODEL section 49).

`confirm_profile` is the ONLY writer: it stores exactly what the student
confirmed, validated by `PlanningProfileInput`, and bumps `version`. Nothing
else - not the planner, not the record, not a future assistant - changes a
profile without that confirmation.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.planning_profile import PlanningProfileInput
from app.models import Student
from app.models.planning_profile import StudentPlanningProfile


def get_profile(session: Session, student: Student) -> StudentPlanningProfile | None:
    return session.scalar(select(StudentPlanningProfile)
                          .where(StudentPlanningProfile.student_id == student.id))


def _plain(value: Decimal) -> Decimal:
    """Numeric(4,1) reads back as 18.0; the student confirmed 18. Same number,
    same text - so a plan's stored constraints compare equal (10.0 -> 10, never 1E+1)."""
    return value.quantize(Decimal(1)) if value == value.to_integral_value() else value.normalize()


def profile_input(row: StudentPlanningProfile) -> PlanningProfileInput:
    return PlanningProfileInput.model_validate({
        "pace": row.pace,
        "target_credits_per_term": _plain(row.target_credits_per_term),
        "max_credits_per_term": _plain(row.max_credits_per_term),
        "max_courses_per_term": row.max_courses_per_term,
        "max_terms": row.max_terms,
        "include_summer": row.include_summer,
        "include_winter": row.include_winter,
        "schedule": row.schedule or {},
        "additional_goals": row.additional_goals or [],
    })


def confirm_profile(session: Session, student: Student, data: PlanningProfileInput,
                    now: datetime, *, source: str = "student_form") -> StudentPlanningProfile:
    row = get_profile(session, student)
    if row is None:
        row = StudentPlanningProfile(student_id=student.id, version=0)
        session.add(row)
    row.version = (row.version or 0) + 1
    row.pace = data.pace.value
    row.target_credits_per_term = data.target_credits_per_term
    row.max_credits_per_term = data.max_credits_per_term
    row.max_courses_per_term = data.max_courses_per_term
    row.max_terms = data.max_terms
    row.include_summer = data.include_summer
    row.include_winter = data.include_winter
    row.schedule = data.schedule.model_dump(mode="json")
    row.additional_goals = [g.model_dump(mode="json") for g in data.additional_goals]
    row.source = source
    row.confirmed_at = now
    session.flush()
    return row


__all__ = ["confirm_profile", "get_profile", "profile_input"]
