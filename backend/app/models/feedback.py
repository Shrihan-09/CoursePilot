"""First-party course feedback and verified external instructor identities (Phase 7.2.4).

Foundation only: no API writes these yet. Feedback is structured (bounded
scales, no free text) so it can only ever surface as an AGGREGATE with a
sample size; no individual answer is exposed (services.course_feedback).
An external instructor profile attaches only through a MANUALLY verified row
here - never by name similarity (services.instructor_identity).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class CourseFeedback(Base, TimestampMixin):
    """One student's structured report on one course they took, in one term."""

    __tablename__ = "course_feedback"
    __table_args__ = (
        UniqueConstraint("student_id", "course_string", "term_code", "instructor_key",
                         name="uq_course_feedback_once"),
        CheckConstraint("workload BETWEEN 1 AND 5", name="ck_feedback_workload"),
        CheckConstraint("difficulty BETWEEN 1 AND 5", name="ck_feedback_difficulty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("student.id", ondelete="CASCADE"), index=True)
    course_string: Mapped[str] = mapped_column(String(32), index=True)
    term_code: Mapped[str] = mapped_column(String(16))
    #: Normalized Rutgers instructor key (services.instructor_identity), or "" if unknown.
    instructor_key: Mapped[str] = mapped_column(String(128), default="", server_default="")
    modality: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 1 very light .. 5 very heavy
    workload: Mapped[int] = mapped_column(SmallInteger)
    #: 1 very easy .. 5 very difficult
    difficulty: Mapped[int] = mapped_column(SmallInteger)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InstructorExternalIdentity(Base, TimestampMixin):
    """A HUMAN-verified link from a Rutgers instructor to an external profile."""

    __tablename__ = "instructor_external_identity"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_instructor_external_profile"),
        CheckConstraint("verification = 'verified_manual'", name="ck_identity_verified_only"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    instructor_key: Mapped[str] = mapped_column(String(128), index=True)
    subject_code: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128))
    verification: Mapped[str] = mapped_column(String(32), default="verified_manual")
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
