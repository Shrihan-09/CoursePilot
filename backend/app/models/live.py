"""CoursePilot Live (Phase 6.9): scheduler, inbox, endpoints, Live Activities, outbox.

    availability_poll_schedule   which Rutgers terms the global refresh polls, how
                                 often, and the LEASE that lets exactly one worker
                                 poll a term at a time (claimed BEFORE the fetch)
    notification                 the in-app inbox item for one notification_event;
                                 read state lives here, never on the factual event
    delivery_endpoint            an account's push address: an APNs device token, a
                                 Live Activity update token, ... (raw token kept to
                                 send; its hash is the identity; logs see a fingerprint)
    live_activity                one ActivityKit activity the app started for a watch
    notification_delivery        the outbox: one row per (event, endpoint, channel),
                                 claimed with a lease, retried with bounded backoff

No price, credit, balance or plan anywhere: a future ledger may point at
these rows, never the reverse. See DATA_MODEL section 45.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

ENDPOINT_KINDS = ("apns_device", "apns_live_activity", "apns_push_to_start", "fcm", "web_push")
ENVIRONMENTS = ("sandbox", "production")
REVOKED_REASONS = ("user_revoked", "rotated", "provider_invalid", "reassigned",
                   "activity_ended", "expired")
ACTIVITY_STATES = ("active", "ended", "expired")
ACTIVITY_END_REASONS = ("user_ended", "watch_deactivated", "expired", "provider_invalid",
                        "opening_delivered", "replaced")
CHANNELS = ("apns_alert", "apns_live_activity")
STATUSES = ("pending", "claimed", "retry", "delivered", "failed_permanent", "suppressed")
SUPPRESSED = ("watch_inactive", "endpoint_revoked", "activity_ended", "event_expired",
              "account_disabled", "live_activity_alerted")
ERROR_CLASSES = ("endpoint_invalid", "request_invalid", "payload_too_large", "provider_auth",
                 "rate_limited", "provider_internal", "provider_unavailable", "network",
                 "timeout", "internal", "unknown", "max_attempts")


def _in(column: str, values) -> str:
    return f"{column} IN ('" + "','".join(values) + "')"


class AvailabilityPollSchedule(Base, TimestampMixin):
    """One Rutgers term the global refresh polls. An operator adds it
    (`live_cli schedule add`) and deactivates it at term end: CoursePilot has
    no other notion of an active term, and Rutgers publishes none."""

    __tablename__ = "availability_poll_schedule"

    term_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    campus: Mapped[str] = mapped_column(String(8), default="NB")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=90)
    backoff_cap_seconds: Mapped[int] = mapped_column(Integer, default=900)
    # The lease: claimed in a short committed transaction BEFORE the Rutgers
    # request, so two workers never poll the same term at once.
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Observability of the last attempt (the refresh log stays the truth).
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_duration_ms: Mapped[int | None] = mapped_column(Integer)
    last_outcome: Mapped[str | None] = mapped_column(String(40))

    __table_args__ = (
        CheckConstraint("interval_seconds >= 30", name="interval_floor"),   # Rutgers max-age=30
        CheckConstraint("backoff_cap_seconds >= interval_seconds", name="cap_above_interval"),
    )


class Notification(Base, TimestampMixin):
    """The inbox item for one event. Reading it never touches the event."""

    __tablename__ = "notification"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"))
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_event.id", ondelete="CASCADE"), unique=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Deliveries were created for it (the fan-out marker; NULL = pending).
    fanned_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_notification_owner_newest", "user_account_id", "created_at", "id"),
        Index("ix_notification_unread", "user_account_id",
              postgresql_where=text("read_at IS NULL"), sqlite_where=text("read_at IS NULL")),
        Index("ix_notification_fanout_pending", "created_at",
              postgresql_where=text("fanned_out_at IS NULL"),
              sqlite_where=text("fanned_out_at IS NULL")),
    )


class DeliveryEndpoint(Base, TimestampMixin):
    """One push address of one account. Revoked, never deleted while
    deliveries reference it; the raw token is cleared on revocation."""

    __tablename__ = "delivery_endpoint"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    environment: Mapped[str] = mapped_column(String(16))
    #: Needed to send; never logged, never returned. NULL once revoked.
    token: Mapped[str | None] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(String(64))
    token_fingerprint: Mapped[str] = mapped_column(String(12))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(32))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("delivery_endpoint.id", ondelete="SET NULL"))

    __table_args__ = (
        CheckConstraint(_in("kind", ENDPOINT_KINDS), name="kind_known"),
        CheckConstraint(_in("environment", ENVIRONMENTS), name="environment_known"),
        CheckConstraint("revoked_reason IS NULL OR " + _in("revoked_reason", REVOKED_REASONS),
                        name="revoked_reason_known"),
        CheckConstraint("(revoked_at IS NULL) = (revoked_reason IS NULL)",
                        name="revoked_with_reason"),
        CheckConstraint("revoked_at IS NOT NULL OR token IS NOT NULL",
                        name="active_has_token"),
        # GLOBAL: one active row per token, whoever registered it - a phone
        # that changes accounts must stop receiving the old account's alerts.
        Index("uq_delivery_endpoint_active_token", "token_hash", unique=True,
              postgresql_where=text("revoked_at IS NULL"),
              sqlite_where=text("revoked_at IS NULL")),
        Index("ix_delivery_endpoint_owner_active", "user_account_id", "kind",
              postgresql_where=text("revoked_at IS NULL"),
              sqlite_where=text("revoked_at IS NULL")),
    )


class LiveActivity(Base, TimestampMixin):
    """An ActivityKit Live Activity the app started for one of its account's
    watches ("Watch Live"). Bounded: Apple ends an activity after 8 hours."""

    __tablename__ = "live_activity"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"))
    watch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("section_watch.id", ondelete="CASCADE"))
    client_activity_id: Mapped[str] = mapped_column(String(128))   # ActivityKit Activity.id
    endpoint_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("delivery_endpoint.id", ondelete="SET NULL"))
    state: Mapped[str] = mapped_column(String(16), default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_reason: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint(_in("state", ACTIVITY_STATES), name="state_known"),
        CheckConstraint("ended_reason IS NULL OR " + _in("ended_reason", ACTIVITY_END_REASONS),
                        name="ended_reason_known"),
        CheckConstraint("(state = 'active') = (ended_at IS NULL)", name="ended_iff_not_active"),
        CheckConstraint("expires_at > started_at", name="bounded"),
        UniqueConstraint("user_account_id", "client_activity_id",
                         name="uq_live_activity_client_id"),
        Index("ix_live_activity_active_watch", "watch_id",
              postgresql_where=text("state = 'active'"), sqlite_where=text("state = 'active'")),
    )


class NotificationDelivery(Base, TimestampMixin):
    """The outbox: one push of one event to one endpoint on one channel.

    At-least-once: a crash after the provider accepted but before the result
    is recorded re-sends after the lease; APNs collapses it by the event id."""

    __tablename__ = "notification_delivery"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    notification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification.id", ondelete="CASCADE"))
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_event.id", ondelete="CASCADE"))
    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("delivery_endpoint.id", ondelete="CASCADE"))
    live_activity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("live_activity.id", ondelete="SET NULL"))
    user_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_class: Mapped[str | None] = mapped_column(String(32))
    provider_status: Mapped[int | None] = mapped_column(Integer)
    provider_reason: Mapped[str | None] = mapped_column(String(64))
    suppressed_reason: Mapped[str | None] = mapped_column(String(32))
    payload_version: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("event_id", "endpoint_id", "channel",
                         name="uq_notification_delivery_event_endpoint_channel"),
        CheckConstraint(_in("channel", CHANNELS), name="channel_known"),
        CheckConstraint(_in("status", STATUSES), name="status_known"),
        CheckConstraint("suppressed_reason IS NULL OR " + _in("suppressed_reason", SUPPRESSED),
                        name="suppressed_reason_known"),
        CheckConstraint("error_class IS NULL OR " + _in("error_class", ERROR_CLASSES),
                        name="error_class_known"),
        CheckConstraint("(status = 'delivered') = (delivered_at IS NOT NULL)",
                        name="delivered_with_time"),
        CheckConstraint("(status = 'suppressed') = (suppressed_reason IS NOT NULL)",
                        name="suppressed_with_reason"),
        CheckConstraint("status <> 'failed_permanent' OR error_class IS NOT NULL",
                        name="failure_classified"),
        CheckConstraint("status <> 'claimed' OR (claim_token IS NOT NULL "
                        "AND claimed_until IS NOT NULL)", name="claim_leased"),
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="attempts_bounded"),
        Index("ix_notification_delivery_due", "next_attempt_at",
              postgresql_where=text("status IN ('pending','retry')"),
              sqlite_where=text("status IN ('pending','retry')")),
        Index("ix_notification_delivery_claimed", "claimed_until",
              postgresql_where=text("status = 'claimed'"),
              sqlite_where=text("status = 'claimed'")),
        Index("ix_notification_delivery_owner", "user_account_id", "created_at"),
    )


__all__ = ["AvailabilityPollSchedule", "DeliveryEndpoint", "LiveActivity", "Notification",
           "NotificationDelivery"]
