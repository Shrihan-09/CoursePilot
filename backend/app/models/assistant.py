"""The assistant's storage (Prompt 11 Part B).

    assistant_conversation   many per student, with editable titles and archive state
    assistant_message        messages linked to the student's own conversation
    assistant_usage          per student per (UTC) day: message count and token
                             COUNTS - the daily cap and cost; never content
    assistant_action         one-time tokens behind "Choose this" / "Confirm":
                             only a SHA-256 is stored, the token goes to the UI
    student_planned_section  the section a student chose for a planned course
                             (B1b step 4) - what "Ready for WebReg" lists

Everything is student-owned and CASCADE-deleted with the student. Chat
history is never written to logs (only counts are).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


def _student_fk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("student.id", ondelete="CASCADE"), index=True)


class AssistantConversation(Base, TimestampMixin):
    __tablename__ = "assistant_conversation"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = _student_fk()
    title: Mapped[str] = mapped_column(String(160), default="New chat")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class AssistantMessage(Base, TimestampMixin):
    __tablename__ = "assistant_message"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("assistant_conversation.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[uuid.UUID] = _student_fk()
    role: Mapped[str] = mapped_column(String(16))              # user | assistant
    content: Mapped[str] = mapped_column(Text)
    #: The rendered reply (cards, questions, proposals with their tokens'
    #: ids, never the raw tokens) and the guided flow's state.
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class AssistantUsage(Base, TimestampMixin):
    __tablename__ = "assistant_usage"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = _student_fk()
    day: Mapped[date] = mapped_column(Date)
    messages: Mapped[int] = mapped_column(Integer, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    escalated_turns: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("student_id", "day", name="uq_assistant_usage_day"),)


class AssistantAction(Base, TimestampMixin):
    __tablename__ = "assistant_action"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = _student_fk()
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("assistant_conversation.id", ondelete="CASCADE"), nullable=True)
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StudentPlannedSection(Base, TimestampMixin):
    __tablename__ = "student_planned_section"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = _student_fk()
    term_code: Mapped[str] = mapped_column(String(5))
    course_string: Mapped[str] = mapped_column(String(32))
    index_number: Mapped[str] = mapped_column(String(16))
    section_number: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: SET NULL: if Rutgers removes the section the choice is reported as no
    #: longer published, never retargeted (models/current_section.py's rule).
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("course_section.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="student_chosen")
    chosen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("student_id", "term_code", "course_string",
                         name="uq_student_planned_section_course"),
        Index("ix_student_planned_section_term", "student_id", "term_code"),
    )


__all__ = ["AssistantAction", "AssistantConversation", "AssistantMessage", "AssistantUsage",
           "StudentPlannedSection"]
