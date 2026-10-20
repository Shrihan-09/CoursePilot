"""The exact Rutgers section a student CONFIRMED for a course they are taking now (7.2.1).

A course on the record ("01:198:211, in progress, Fall 2026") does not
identify a section: a Rutgers transcript carries at most a section NUMBER
(kept only in import staging, a hint), manual entry carries none, and a course
may have dozens of sections. CoursePilot therefore NEVER infers one. A row
exists only after the student picks their section from the Rutgers list for
that term - the same rule as a section watch (models/monitoring.py).

* `student_course_id` (CASCADE): the in-progress record row it belongs to. If
  that row is deleted or no longer in progress, the confirmation is moot (the
  API ignores rows whose course is not in progress).
* `section_id` (SET NULL) + the natural key `term_code` / `index_number`: if
  Rutgers removes the section (or ingestion replaces the row), the link goes
  NULL and the confirmation is reported as "section no longer published" - it
  is never retargeted to another section.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class StudentCurrentSection(Base, TimestampMixin):
    __tablename__ = "student_current_section"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("student.id", ondelete="CASCADE"), index=True
    )
    student_course_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("student_course.id", ondelete="CASCADE")
    )
    term_code: Mapped[str] = mapped_column(String(16))
    course_string: Mapped[str] = mapped_column(String(32))
    index_number: Mapped[str] = mapped_column(String(16))
    #: primary | required_companion - a Rutgers-linked lab/recitation has its
    #: own index, so a course may carry one confirmation per component.
    section_number: Mapped[str | None] = mapped_column(String(16), nullable=True)
    supplement_code: Mapped[str] = mapped_column(String(16), default="", server_default="")
    component: Mapped[str] = mapped_column(String(32), default="primary")
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("course_section.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(32), default="student_confirmed")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "student_course_id",
            "component",
            "supplement_code",
            name="uq_student_current_section_course_component",
        ),
    )

    def __repr__(self) -> str:
        return f"<StudentCurrentSection {self.course_string} {self.term_code} #{self.index_number}>"


__all__ = ["StudentCurrentSection"]
