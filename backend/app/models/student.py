"""Minimal student academic record.

Scope is deliberately tiny: enough to run a degree audit, nothing more. No
authentication, no accounts, no profile, no PII beyond an opaque external
reference. Student data is a liability; this phase needs only a fixture
student to prove the audit engine works.

`StudentCourse` covers completed, in-progress, and planned courses in one
table because they differ only by status and by whether a grade exists -
splitting them into three tables would triple the query surface for no gain.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class EnrollmentStatus(StrEnum):
    """Why all three live in one table.

    The audit treats them differently - completed courses satisfy a
    requirement, in-progress ones can only *provisionally* satisfy it, and
    planned ones satisfy nothing yet - but that is audit logic, not a storage
    difference.
    """

    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    PLANNED = "planned"


class Student(Base, TimestampMixin):
    """Deliberately thin. Authentication is out of scope and must land before
    this table holds a real person."""

    __tablename__ = "student"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    external_ref: Mapped[str | None] = mapped_column(String(64))

    # The catalog year whose rules bind this student. Stored explicitly rather
    # than inferred from a matriculation date: the binding is an academic
    # decision, and inferring it would silently pick the wrong ruleset.
    catalog_year: Mapped[str] = mapped_column(String(16))

    program_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("program_version.id"), index=True
    )

    courses: Mapped[list[StudentCourse]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("external_ref", name="uq_student_external_ref"),)

    def __repr__(self) -> str:
        return f"<Student {self.external_ref or self.id}>"


class StudentCourse(Base, TimestampMixin):
    """One course on a student's record.

    Natural key: (student_id, course_id, term_code). A course may legitimately
    appear twice on a record in DIFFERENT terms (a retake), so term is part of
    identity. Whether a retake counts twice toward a requirement is an audit
    rule, not a storage rule.
    """

    __tablename__ = "student_course"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course.id", ondelete="CASCADE"), index=True
    )

    term_code: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))

    grade: Mapped[str | None] = mapped_column(String(4))
    # Credits actually earned. May differ from the course's catalog credits
    # (variable-credit courses, transfer credit), so it is stored, not derived.
    credits_earned: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))

    # Student-reported data is evidence, not fact. Defaults to self-reported
    # so an unverified record can never masquerade as a registrar record.
    source_kind: Mapped[str] = mapped_column(String(32), default="student_self_reported")

    student: Mapped[Student] = relationship(back_populates="courses")

    __table_args__ = (
        UniqueConstraint("student_id", "course_id", "term_code", name="uq_student_course_term"),
        CheckConstraint(
            "status IN ('completed','in_progress','planned')", name="student_course_status_known"
        ),
        CheckConstraint(
            "credits_earned IS NULL OR credits_earned >= 0", name="credits_earned_non_negative"
        ),
        # A completed course without a grade is a data error we want caught at
        # the boundary; planned/in-progress courses legitimately have none.
        CheckConstraint(
            "status <> 'completed' OR grade IS NOT NULL", name="completed_requires_grade"
        ),
        Index("ix_student_course_status", "student_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<StudentCourse {self.course_id} {self.status}>"
