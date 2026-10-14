"""Section watches and notification events (Phase 6.8).

Four layers, four owners - none of them user-specific below the watch:

    section_availability_change   system history (6.7): one row per observed transition
    availability_change_dispatch  system: "this opening was turned into events" (one per change)
    section_watch                 user intent: "tell me when THIS term's section opens"
    notification_event            user fact: one row per (watch, opening) - never per poll

A watch carries no price, balance, payment or plan - a future credit ledger
authorizes CREATING a watch from outside (it may reference the watch; the
watch never references it). An event carries no delivery state - a future
notification_delivery (event x channel/endpoint) owns attempts and retries;
the event id is its idempotency key. See DATA_MODEL section 44.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

DEACTIVATION_REASONS = ("user_cancelled", "term_ended", "target_removed")
EVENT_KINDS = ("section_opened",)
_ACTIVE = text("deactivated_at IS NULL")


class SectionWatch(Base, TimestampMixin):
    """One user wants CoursePilot to watch one term-scoped section.

    Deactivated, never deleted: the history of what a user watched and why it
    stopped (cancelled, term ended, section removed) stays auditable."""

    __tablename__ = "section_watch"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # The account, not the student: watching needs no academic record, and
    # per-user data hangs off user_account. Deleting the account removes it.
    user_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"))
    # SET NULL + the natural key beside it (like section_availability_change):
    # a section deleted from CoursePilot leaves the watch's history intact.
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("course_section.id", ondelete="SET NULL"))
    term_code: Mapped[str] = mapped_column(String(16))
    index_number: Mapped[str] = mapped_column(String(16))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivation_reason: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint("deactivation_reason IS NULL OR deactivation_reason IN ('"
                        + "','".join(DEACTIVATION_REASONS) + "')", name="reason_known"),
        CheckConstraint("(deactivated_at IS NULL) = (deactivation_reason IS NULL)",
                        name="deactivated_with_reason"),
        # One ACTIVE watch per user per term-scoped target; inactive ones are history.
        Index("uq_section_watch_active_target", "user_account_id", "term_code",
              "index_number", unique=True, postgresql_where=_ACTIVE, sqlite_where=_ACTIVE),
        # The processor's join: active watches of a section.
        Index("ix_section_watch_active_section", "section_id",
              postgresql_where=_ACTIVE, sqlite_where=_ACTIVE),
        Index("ix_section_watch_owner", "user_account_id", "created_at"),
    )


class AvailabilityChangeDispatch(Base, TimestampMixin):
    """Marks one opening (a closed -> open change row) as processed.

    The change table stays append-only; this side table is how the processor
    finds what is left to do after a crash, whatever order refreshes
    committed in (a time cursor would skip a late-committing refresh)."""

    __tablename__ = "availability_change_dispatch"

    change_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("section_availability_change.id", ondelete="RESTRICT"), primary_key=True)
    events_created: Mapped[int] = mapped_column(Integer)


class NotificationEvent(Base, TimestampMixin):
    """A durable, channel-neutral fact for ONE user: a watched section was
    observed going closed -> open. At most one per (watch, change) - the
    unique key, not application logic, guarantees it."""

    __tablename__ = "notification_event"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"))
    watch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("section_watch.id", ondelete="CASCADE"))
    change_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("section_availability_change.id", ondelete="RESTRICT"))
    refresh_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("availability_refresh.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(32))
    # What a client renders - structured, never prose to parse.
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("course_section.id", ondelete="SET NULL"))
    term_code: Mapped[str] = mapped_column(String(16))
    index_number: Mapped[str] = mapped_column(String(16))
    course_string: Mapped[str] = mapped_column(String(16))
    supplement_code: Mapped[str] = mapped_column(String(8))
    section_number: Mapped[str] = mapped_column(String(16))
    from_status: Mapped[str] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The registration bundle around the opened index, at processing time:
    # the OTHER required record(s) of the same course (an "LB" lab, or the
    # lecture when the lab is watched). NULL when the course has none.
    required_component_state: Mapped[str | None] = mapped_column(String(16))
    required_components_open: Mapped[int | None] = mapped_column(Integer)
    required_components_total: Mapped[int | None] = mapped_column(Integer)
    context_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("watch_id", "change_id", name="uq_notification_event_watch_change"),
        CheckConstraint("kind IN ('" + "','".join(EVENT_KINDS) + "')", name="kind_known"),
        CheckConstraint("required_component_state IS NULL OR required_component_state "
                        "IN ('open','closed','unknown')", name="component_state_known"),
        Index("ix_notification_event_owner", "user_account_id", "observed_at"),
    )


__all__ = ["DEACTIVATION_REASONS", "EVENT_KINDS", "AvailabilityChangeDispatch",
           "NotificationEvent", "SectionWatch"]
