"""Private schedule staging and explicitly confirmed registration identity."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ScheduleImport(Base, TimestampMixin):
    __tablename__ = "schedule_import"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("student.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[str] = mapped_column(String(24), default="extracted")
    version: Mapped[int] = mapped_column(Integer, default=1)
    content_sha256: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Bounded academic identifiers/issues only; no raw text or personal names.
    rows: Mapped[list] = mapped_column(JSON, default=list)


class StudentRegistration(Base, TimestampMixin):
    __tablename__ = "student_registration"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("student.id", ondelete="CASCADE"), index=True
    )
    term_code: Mapped[str] = mapped_column(String(16))
    course_string: Mapped[str] = mapped_column(String(32))
    section_number: Mapped[str] = mapped_column(String(16))
    supplement_code: Mapped[str] = mapped_column(String(16), default="", server_default="")
    index_number: Mapped[str | None] = mapped_column(String(16), nullable=True)
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("course_section.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(32), default="student_schedule_pdf")
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("schedule_import.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "term_code",
            "course_string",
            "section_number",
            "supplement_code",
            name="uq_registration_identity",
        ),
    )
