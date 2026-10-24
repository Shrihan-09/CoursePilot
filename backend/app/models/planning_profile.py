"""A student's confirmed Planning Profile (Phase 7.2.1; DATA_MODEL section 49).

STUDENT INTENT, never academic truth: pace, target and maximum load, seasons,
schedule defaults and exploratory goals. One row per student, written only
when the student confirms (`version` increments on every confirmation), read
by the planner through `app.domain.planning_profile.to_constraints`.

`source` records how the confirmed values were arrived at - "student_form"
today; a future assistant's proposal would be "assistant_proposal", and is
still stored only after the student confirms it.

ON DELETE CASCADE from `student`: a preference does not outlive its student.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class StudentPlanningProfile(Base, TimestampMixin):
    __tablename__ = "student_planning_profile"

    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("student.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    pace: Mapped[str] = mapped_column(String(32))
    #: Academic strategy (Phase 7.2.4): a RANKING preference among valid courses.
    strategy: Mapped[str] = mapped_column(String(32), default="balanced", server_default="balanced")
    target_credits_per_term: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    max_credits_per_term: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    max_courses_per_term: Mapped[int] = mapped_column(Integer)
    max_terms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    include_summer: Mapped[bool] = mapped_column(Boolean, default=False)
    include_winter: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule: Mapped[dict] = mapped_column(JSON)
    additional_goals: Mapped[list] = mapped_column(JSON)
    #: Phase 7.3: per-term intensity (NULL: confirmed before 7.3 - pace behaviour),
    #: how fillers are chosen, and courses pinned to terms (hard constraints).
    workload_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    filler_preference: Mapped[str] = mapped_column(String(16), default="no_preference",
                                                   server_default="no_preference")
    pins: Mapped[list] = mapped_column(JSON, default=list)
    #: Phase 7.3: optional expected graduation term ("20281"), overriding the pace.
    expected_graduation_term: Mapped[str | None] = mapped_column(String(5), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="student_form")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<StudentPlanningProfile {self.student_id} v{self.version} {self.pace}>"


__all__ = ["StudentPlanningProfile"]
