"""CoursePilot Live (Phase 6.9): inbox, endpoints, Live Activities, outbox, scheduler lease.

Each function owns ONE responsibility and leaves transactions to its caller
unless it says otherwise; nothing here contacts Rutgers, and only `deliver_due`
contacts a push provider - outside any database transaction.

    refresh (6.7) -> process_openings (6.8: events + inbox items)
                  -> fan_out        (deliveries per active endpoint)
                  -> deliver_due    (claim -> re-check -> send -> record)

Ownership always comes from the caller's account id (the credential), and
another account's row is indistinguishable from a missing one.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.observability import redact_id
from app.core.registration_windows import poll_interval
from app.domain.live import (
    EVENT_TTL,
    LEASE,
    LIVE_ACTIVITY_MAX,
    MAX_ACTIVE_LIVE_ACTIVITIES,
    MAX_ATTEMPTS,
    MAX_ENDPOINTS_PER_ACCOUNT,
    PAYLOAD_VERSION,
    Channel,
    EndpointKind,
    ErrorClass,
    OpeningFacts,
    Outcome,
    PayloadTooLarge,
    SendResult,
    SuppressedReason,
    alert_payload,
    live_activity_payload,
    next_attempt,
    token_fingerprint,
    token_hash,
    valid_token,
)
from app.models import UserAccount
from app.models.availability import AvailabilityRefresh
from app.models.live import (
    AvailabilityPollSchedule,
    DeliveryEndpoint,
    LiveActivity,
    Notification,
    NotificationDelivery,
)
from app.models.monitoring import NotificationEvent, SectionWatch
from app.services.push_providers import PushProvider, PushRequest

logger = logging.getLogger(__name__)


class LiveError(ValueError):
    """A request that cannot be honoured. The message is client-safe."""


class NotFound(LiveError):
    pass


class Invalid(LiveError):
    pass


def _update(model):
    """A bulk UPDATE whose session sync re-reads affected rows from the
    database instead of re-evaluating the WHERE clause in Python (SQLite
    returns naive datetimes; comparing them with aware ones would raise)."""
    return update(model).execution_options(synchronize_session="fetch")


def _utc(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=UTC)


def _insert_returning(session: Session, table, rows: list[dict], keys: list[str],
                      returning) -> list:
    """INSERT ... ON CONFLICT DO NOTHING RETURNING on PostgreSQL and SQLite."""
    if not rows:
        return []
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(table).values(rows).on_conflict_do_nothing(index_elements=keys)
    return session.execute(stmt.returning(*returning)).all()


# ==========================================================================
# inbox
# ==========================================================================


def create_inbox_items(session: Session, events: list[tuple[uuid.UUID, uuid.UUID]],
                       now: datetime) -> int:
    """One inbox item per NEWLY created event (event_id, account) - called by
    the 6.8 processor inside the same transaction, so "event exists" implies
    "inbox item exists". Idempotent by UNIQUE(event_id)."""
    rows = [{"id": uuid.uuid4(), "user_account_id": account, "event_id": event,
             "created_at": now, "updated_at": now} for event, account in events]
    return len(_insert_returning(session, Notification.__table__, rows, ["event_id"],
                                 [Notification.__table__.c.id]))


def list_notifications(session: Session, account_id, *, limit: int,
                       before: tuple[datetime, uuid.UUID] | None = None):
    """Newest first, keyset-paged on (created_at, id) - a late-processed older
    opening never slides above a page the client already read."""
    q = (select(Notification, NotificationEvent)
         .join(NotificationEvent, NotificationEvent.id == Notification.event_id)
         .where(Notification.user_account_id == account_id))
    if before is not None:
        q = q.where(or_(Notification.created_at < before[0],
                        and_(Notification.created_at == before[0], Notification.id < before[1])))
    return session.execute(q.order_by(Notification.created_at.desc(), Notification.id.desc())
                           .limit(limit)).all()


def unread_count(session: Session, account_id) -> int:
    return session.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_account_id == account_id, Notification.read_at.is_(None))) or 0


def mark_read(session: Session, account_id, notification_id, *, now: datetime):
    """Idempotent. Writes the INBOX row only - the factual event is untouched."""
    item = session.scalar(select(Notification).where(
        Notification.id == notification_id, Notification.user_account_id == account_id))
    if item is None:
        raise NotFound("No such notification.")
    if item.read_at is None:
        item.read_at = now
        session.flush()
    return item


def mark_all_read(session: Session, account_id, *, now: datetime,
                  up_to: datetime | None = None) -> int:
    """Marks items created up to `up_to` (default now) - never one the client
    has not seen yet."""
    result = session.execute(_update(Notification).where(
        Notification.user_account_id == account_id, Notification.read_at.is_(None),
        Notification.created_at <= (up_to or now)).values(read_at=now, updated_at=now))
    return result.rowcount or 0


# ==========================================================================
# endpoints
# ==========================================================================

REGISTRABLE = (EndpointKind.APNS_DEVICE, EndpointKind.APNS_PUSH_TO_START)


def _revoke(endpoint: DeliveryEndpoint, reason: str, now: datetime,
            replaced_by: uuid.UUID | None = None) -> None:
    if endpoint.revoked_at is None:
        endpoint.revoked_at, endpoint.revoked_reason = now, reason
        endpoint.token = None                    # keep the hash, drop the secret
        endpoint.replaced_by_id = replaced_by


def _add_endpoint(session: Session, account_id, kind: str, environment: str, token: str,
                  now: datetime) -> DeliveryEndpoint:
    """Insert (or refresh) the active endpoint for this token.

    Same owner: refreshed. Another owner: that row is revoked ('reassigned')
    first - a device that changes accounts must stop receiving the old
    account's alerts. The partial unique index is the final authority."""
    if not valid_token(token):
        raise Invalid("Token must be a hexadecimal APNs/ActivityKit token.")
    digest = token_hash(token)
    current = session.scalar(select(DeliveryEndpoint).where(
        DeliveryEndpoint.token_hash == digest, DeliveryEndpoint.revoked_at.is_(None)))
    if current is not None and current.user_account_id == account_id \
            and current.kind == kind and current.environment == environment:
        current.last_seen_at = now
        session.flush()
        return current
    if current is not None:
        _revoke(current, "reassigned", now)
        session.flush()
    active = session.scalar(select(func.count()).select_from(DeliveryEndpoint).where(
        DeliveryEndpoint.user_account_id == account_id, DeliveryEndpoint.revoked_at.is_(None)))
    if active >= MAX_ENDPOINTS_PER_ACCOUNT:
        raise Invalid(f"At most {MAX_ENDPOINTS_PER_ACCOUNT} active push endpoints.")
    endpoint = DeliveryEndpoint(
        id=uuid.uuid4(), user_account_id=account_id, kind=kind, environment=environment,
        token=token.lower(), token_hash=digest, token_fingerprint=token_fingerprint(token),
        last_seen_at=now, created_at=now, updated_at=now)
    try:
        with session.begin_nested():
            session.add(endpoint)
    except IntegrityError:
        raise Invalid("This token was registered concurrently; retry.") from None
    return endpoint


def register_endpoint(session: Session, account_id, *, kind: str, environment: str,
                      token: str, now: datetime,
                      replaces_endpoint_id: uuid.UUID | None = None) -> DeliveryEndpoint:
    """A device push token (or a stored push-to-start token). Rotation: the
    replaced endpoint - which must be the caller's - is revoked 'rotated'."""
    if kind not in {k.value for k in REGISTRABLE}:
        raise Invalid(f"kind must be one of {[k.value for k in REGISTRABLE]}.")
    old = None
    if replaces_endpoint_id is not None:
        old = _owned_endpoint(session, account_id, replaces_endpoint_id)
    endpoint = _add_endpoint(session, account_id, kind, environment, token, now)
    if old is not None and old.id != endpoint.id:
        _revoke(old, "rotated", now, endpoint.id)
        _suppress_pending_for(session, [old.id], SuppressedReason.ENDPOINT_REVOKED, now)
    session.flush()
    return endpoint


def _owned_endpoint(session: Session, account_id, endpoint_id) -> DeliveryEndpoint:
    endpoint = session.scalar(select(DeliveryEndpoint).where(
        DeliveryEndpoint.id == endpoint_id, DeliveryEndpoint.user_account_id == account_id))
    if endpoint is None:
        raise NotFound("No such endpoint.")
    return endpoint


def revoke_endpoint(session: Session, account_id, endpoint_id, *, now: datetime):
    endpoint = _owned_endpoint(session, account_id, endpoint_id)
    _revoke(endpoint, "user_revoked", now)
    _suppress_pending_for(session, [endpoint.id], SuppressedReason.ENDPOINT_REVOKED, now)
    for activity in session.scalars(select(LiveActivity).where(
            LiveActivity.endpoint_id == endpoint.id, LiveActivity.state == "active")):
        _end_activity(activity, "user_ended", now)
    session.flush()
    return endpoint


def list_endpoints(session: Session, account_id):
    return session.scalars(select(DeliveryEndpoint).where(
        DeliveryEndpoint.user_account_id == account_id, DeliveryEndpoint.revoked_at.is_(None))
        .order_by(DeliveryEndpoint.created_at.desc(), DeliveryEndpoint.id)).all()


def _suppress_pending_for(session: Session, endpoint_ids, reason: SuppressedReason,
                          now: datetime) -> None:
    if endpoint_ids:
        session.execute(_update(NotificationDelivery).where(
            NotificationDelivery.endpoint_id.in_(list(endpoint_ids)),
            NotificationDelivery.status.in_(["pending", "retry"]))
            .values(status="suppressed", suppressed_reason=reason.value, updated_at=now))


# ==========================================================================
# Live Activities
# ==========================================================================


def _end_activity(activity: LiveActivity, reason: str, now: datetime,
                  state: str = "ended") -> None:
    if activity.state == "active":
        activity.state, activity.ended_at, activity.ended_reason = state, now, reason


def register_live_activity(session: Session, account_id, *, watch_id, client_activity_id: str,
                           push_token: str, environment: str, now: datetime) -> LiveActivity:
    """The app started an ActivityKit activity for one of ITS watches and
    received the activity's push token. Idempotent per client activity id."""
    watch = session.scalar(select(SectionWatch).where(
        SectionWatch.id == watch_id, SectionWatch.user_account_id == account_id))
    if watch is None:
        raise NotFound("No such watch.")                # another account's watch: same answer
    if watch.deactivated_at is not None:
        raise Invalid("The watch is no longer active.")
    existing = session.scalar(select(LiveActivity).where(
        LiveActivity.user_account_id == account_id,
        LiveActivity.client_activity_id == client_activity_id))
    if existing is not None:
        if existing.watch_id != watch.id:
            raise Invalid("That activity belongs to another watch.")
        if existing.state == "active":
            rotate_live_activity_token(session, account_id, existing.id,
                                       push_token=push_token, environment=environment, now=now)
        return existing
    active = session.scalar(select(func.count()).select_from(LiveActivity).where(
        LiveActivity.user_account_id == account_id, LiveActivity.state == "active"))
    if active >= MAX_ACTIVE_LIVE_ACTIVITIES:
        raise Invalid(f"At most {MAX_ACTIVE_LIVE_ACTIVITIES} active Live Activities.")
    endpoint = _add_endpoint(session, account_id, EndpointKind.APNS_LIVE_ACTIVITY.value,
                             environment, push_token, now)
    activity = LiveActivity(id=uuid.uuid4(), user_account_id=account_id, watch_id=watch.id,
                            client_activity_id=client_activity_id, endpoint_id=endpoint.id,
                            state="active", started_at=now, expires_at=now + LIVE_ACTIVITY_MAX,
                            created_at=now, updated_at=now)
    session.add(activity)
    session.flush()
    return activity


def _owned_activity(session: Session, account_id, activity_id) -> LiveActivity:
    activity = session.scalar(select(LiveActivity).where(
        LiveActivity.id == activity_id, LiveActivity.user_account_id == account_id))
    if activity is None:
        raise NotFound("No such Live Activity.")
    return activity


def rotate_live_activity_token(session: Session, account_id, activity_id, *, push_token: str,
                               environment: str, now: datetime) -> LiveActivity:
    """ActivityKit's pushTokenUpdates delivered a new token: Apple says to
    invalidate the previous one. Old endpoint 'rotated', pending deliveries
    to it suppressed, the activity repointed - in one transaction."""
    activity = _owned_activity(session, account_id, activity_id)
    if activity.state != "active":
        raise Invalid("The Live Activity has ended.")
    old = session.get(DeliveryEndpoint, activity.endpoint_id) if activity.endpoint_id else None
    if old is not None and old.token_hash == token_hash(push_token) and old.revoked_at is None:
        old.last_seen_at = now
        session.flush()
        return activity
    new = _add_endpoint(session, account_id, EndpointKind.APNS_LIVE_ACTIVITY.value,
                        environment, push_token, now)
    if old is not None:
        _revoke(old, "rotated", now, new.id)
        _suppress_pending_for(session, [old.id], SuppressedReason.ENDPOINT_REVOKED, now)
    activity.endpoint_id = new.id
    session.flush()
    return activity


def end_live_activity(session: Session, account_id, activity_id, *, now: datetime):
    activity = _owned_activity(session, account_id, activity_id)
    _close_activities(session, [activity], "user_ended", now)
    return activity


def _close_activities(session: Session, activities, reason: str, now: datetime,
                      state: str = "ended") -> int:
    count = 0
    for activity in activities:
        if activity.state != "active":
            continue
        _end_activity(activity, reason, now, state)
        if activity.endpoint_id:
            endpoint = session.get(DeliveryEndpoint, activity.endpoint_id)
            if endpoint is not None:
                _revoke(endpoint, "expired" if state == "expired" else "activity_ended", now)
                _suppress_pending_for(session, [endpoint.id], SuppressedReason.ACTIVITY_ENDED,
                                      now)
        count += 1
    session.flush()
    return count


def end_activities_for_watches(session: Session, watch_ids, *, now: datetime) -> int:
    """A watch stopped (cancelled, term ended, section removed): its Live
    Activities end and their tokens are revoked."""
    ids = [w for w in watch_ids if w is not None]
    if not ids:
        return 0
    activities = session.scalars(select(LiveActivity).where(
        LiveActivity.watch_id.in_(ids), LiveActivity.state == "active")).all()
    return _close_activities(session, activities, "watch_deactivated", now)


def expire_live_activities(session: Session, *, now: datetime) -> int:
    """Apple ends an activity after 8 hours; the server stops targeting it too."""
    activities = session.scalars(select(LiveActivity).where(
        LiveActivity.state == "active", LiveActivity.expires_at <= now)
        .order_by(LiveActivity.expires_at, LiveActivity.id)).all()
    return _close_activities(session, activities, "expired", now, state="expired")


def list_live_activities(session: Session, account_id):
    return session.scalars(select(LiveActivity).where(
        LiveActivity.user_account_id == account_id, LiveActivity.state == "active")
        .order_by(LiveActivity.started_at.desc(), LiveActivity.id)).all()


# ==========================================================================
# fan-out: inbox items -> deliveries (set-based, idempotent)
# ==========================================================================


@dataclass
class FanoutStats:
    notifications: int = 0
    deliveries: int = 0
    suppressed: int = 0
    batches: int = 0


def _pending_fanout(session: Session, batch: int):
    q = (select(Notification).where(Notification.fanned_out_at.is_(None))
         .order_by(Notification.created_at, Notification.id).limit(batch))
    if session.get_bind().dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    return session.scalars(q).all()


def fan_out(session: Session, *, now: datetime, batch: int = 500,
            commit: bool = True) -> FanoutStats:
    """Create one delivery per (event, active endpoint, channel).

      * the watch's ACTIVE Live Activity endpoints -> apns_live_activity
      * the account's active device endpoints     -> apns_alert, unless a Live
        Activity update carries the alert (Apple HIG: don't alert twice) -
        then the alert row is recorded as suppressed 'live_activity_alerted'
      * an event older than EVENT_TTL             -> suppressed 'event_expired'

    Endpoints registered after an event never receive it (the inbox is the
    catch-up). A fixed number of statements per batch, whatever the count."""
    stats = FanoutStats()
    handled: set = set()
    while True:
        items = _pending_fanout(session, batch)
        if not items:
            break
        if {i.id for i in items} <= handled:
            raise RuntimeError("fan-out markers were not recorded")
        handled |= {i.id for i in items}
        stats.batches += 1
        stats.notifications += len(items)
        events = {e.id: e for e in session.scalars(select(NotificationEvent).where(
            NotificationEvent.id.in_([i.event_id for i in items])))}
        accounts = {i.user_account_id for i in items}
        devices = defaultdict(list)
        for d in session.scalars(select(DeliveryEndpoint).where(
                DeliveryEndpoint.user_account_id.in_(accounts),
                DeliveryEndpoint.kind == EndpointKind.APNS_DEVICE.value,
                DeliveryEndpoint.revoked_at.is_(None))
                .order_by(DeliveryEndpoint.created_at, DeliveryEndpoint.id)):
            devices[d.user_account_id].append(d)
        activities = defaultdict(list)
        for a, d in session.execute(
                select(LiveActivity, DeliveryEndpoint)
                .join(DeliveryEndpoint, DeliveryEndpoint.id == LiveActivity.endpoint_id)
                .where(LiveActivity.watch_id.in_({e.watch_id for e in events.values()}),
                       LiveActivity.state == "active", DeliveryEndpoint.revoked_at.is_(None))
                .order_by(LiveActivity.started_at, LiveActivity.id)):
            activities[a.watch_id].append((a, d))
        rows = []
        for item in items:
            event = events[item.event_id]
            expired = now > _utc(event.observed_at) + EVENT_TTL
            live = activities.get(event.watch_id, [])
            base = {"notification_id": item.id, "event_id": event.id,
                    "user_account_id": item.user_account_id, "attempts": 0,
                    "max_attempts": MAX_ATTEMPTS, "next_attempt_at": now,
                    "payload_version": PAYLOAD_VERSION, "created_at": now, "updated_at": now}
            for activity, endpoint in live:
                rows.append({**base, "id": uuid.uuid4(), "endpoint_id": endpoint.id,
                             "live_activity_id": activity.id,
                             "channel": Channel.APNS_LIVE_ACTIVITY.value,
                             **_initial(expired, None)})
            for endpoint in devices.get(item.user_account_id, []):
                reason = SuppressedReason.LIVE_ACTIVITY_ALERTED if live else None
                rows.append({**base, "id": uuid.uuid4(), "endpoint_id": endpoint.id,
                             "live_activity_id": None, "channel": Channel.APNS_ALERT.value,
                             **_initial(expired, reason)})
        created = _insert_returning(
            session, NotificationDelivery.__table__, rows, ["event_id", "endpoint_id", "channel"],
            [NotificationDelivery.__table__.c.status])
        stats.deliveries += len(created)
        stats.suppressed += sum(1 for (s,) in created if s == "suppressed")
        session.execute(_update(Notification).where(Notification.id.in_([i.id for i in items]))
                        .values(fanned_out_at=now, updated_at=now))
        if commit:
            session.commit()
        else:
            session.flush()
    return stats


def _initial(expired: bool, reason: SuppressedReason | None) -> dict:
    if expired:
        reason = SuppressedReason.EVENT_EXPIRED
    if reason is None:
        return {"status": "pending", "suppressed_reason": None}
    return {"status": "suppressed", "suppressed_reason": reason.value}


# ==========================================================================
# delivery: claim -> re-check -> send (no transaction open) -> record (fenced)
# ==========================================================================


@dataclass
class DeliverStats:
    claimed: int = 0
    delivered: int = 0
    retried: int = 0
    failed: int = 0
    suppressed: int = 0
    endpoints_revoked: int = 0
    lost_lease: int = 0
    by_error: dict[str, int] = field(default_factory=dict)


def claim(session: Session, *, now: datetime, limit: int, lease: timedelta = LEASE):
    """Claim due deliveries (and ones whose lease expired - a crashed worker)
    in ONE short committed transaction. The attempt is counted at claim, so a
    worker that dies mid-send still uses an attempt: retries stay bounded."""
    due = (select(NotificationDelivery.id).where(or_(
        and_(NotificationDelivery.status.in_(["pending", "retry"]),
             NotificationDelivery.next_attempt_at <= now),
        and_(NotificationDelivery.status == "claimed", NotificationDelivery.claimed_until < now)))
        .where(NotificationDelivery.attempts < NotificationDelivery.max_attempts)
        .order_by(NotificationDelivery.next_attempt_at, NotificationDelivery.id).limit(limit))
    if session.get_bind().dialect.name == "postgresql":
        due = due.with_for_update(skip_locked=True)
    ids = session.scalars(due).all()
    if not ids:
        session.commit()
        return []
    token = uuid.uuid4()
    session.execute(_update(NotificationDelivery).where(NotificationDelivery.id.in_(ids))
                    .values(status="claimed", claim_token=token, claimed_until=now + lease,
                            attempts=NotificationDelivery.attempts + 1, last_attempt_at=now,
                            updated_at=now))
    session.commit()
    return [(i, token) for i in ids]


def _exhausted(session: Session, *, now: datetime) -> int:
    """A lease that expired on the LAST allowed attempt can never be retried:
    it becomes failed_permanent (max_attempts) instead of staying claimed."""
    result = session.execute(_update(NotificationDelivery).where(
        NotificationDelivery.status == "claimed", NotificationDelivery.claimed_until < now,
        NotificationDelivery.attempts >= NotificationDelivery.max_attempts)
        .values(status="failed_permanent", error_class=ErrorClass.MAX_ATTEMPTS.value,
                claim_token=None, claimed_until=None, updated_at=now))
    return result.rowcount or 0


def _facts(event: NotificationEvent) -> OpeningFacts:
    return OpeningFacts(
        event_id=str(event.id), watch_id=str(event.watch_id), term_code=event.term_code,
        course_string=event.course_string, supplement_code=event.supplement_code,
        section_number=event.section_number, index_number=event.index_number,
        observed_at=_utc(event.observed_at), component_state=event.required_component_state,
        component_open=event.required_components_open,
        component_total=event.required_components_total,
        component_as_of=_utc(event.context_as_of))


def _recheck(session: Session, delivery: NotificationDelivery, now: datetime):
    """(facts, endpoint) to send, or a SuppressedReason. Read-only, ONE query,
    run immediately before this delivery's send (never batched ahead of it:
    a watch cancelled a moment ago must still suppress)."""
    event, watch, endpoint, account, activity = session.execute(
        select(NotificationEvent, SectionWatch, DeliveryEndpoint, UserAccount, LiveActivity)
        .select_from(NotificationEvent)
        .outerjoin(SectionWatch, SectionWatch.id == NotificationEvent.watch_id)
        .outerjoin(DeliveryEndpoint, DeliveryEndpoint.id == delivery.endpoint_id)
        .outerjoin(UserAccount, UserAccount.id == delivery.user_account_id)
        .outerjoin(LiveActivity, LiveActivity.id == delivery.live_activity_id)
        .where(NotificationEvent.id == delivery.event_id)
        .execution_options(populate_existing=True)).one()
    if watch is None or watch.deactivated_at is not None:
        return SuppressedReason.WATCH_INACTIVE
    if account is None or account.disabled_at is not None:
        return SuppressedReason.ACCOUNT_DISABLED
    if endpoint is None or endpoint.revoked_at is not None or not endpoint.token:
        return SuppressedReason.ENDPOINT_REVOKED
    if delivery.live_activity_id is not None and (
            activity is None or activity.state != "active"
            or (activity.expires_at and _utc(activity.expires_at) <= now)):
        return SuppressedReason.ACTIVITY_ENDED
    if now > _utc(event.observed_at) + EVENT_TTL:
        return SuppressedReason.EVENT_EXPIRED
    return _facts(event), endpoint


def _record(session: Session, delivery_id, token, values: dict) -> bool:
    """Fenced on the claim: a worker whose lease was taken over writes nothing."""
    result = session.execute(_update(NotificationDelivery).where(
        NotificationDelivery.id == delivery_id, NotificationDelivery.claim_token == token,
        NotificationDelivery.status == "claimed")
        .values(claim_token=None, claimed_until=None, **values))
    return bool(result.rowcount)


def deliver_due(session: Session, provider: PushProvider, *, now: datetime,
                limit: int = 100) -> DeliverStats:
    """One pass over due deliveries. Each is independent: one endpoint's
    failure is recorded on its own row and never stops the others."""
    stats = DeliverStats()
    _exhausted(session, now=now)
    session.commit()
    for delivery_id, token in claim(session, now=now, limit=limit):
        stats.claimed += 1
        try:
            outcome = _deliver_one(session, provider, delivery_id, token, now, stats)
        except Exception:                      # a bug, never a token: logged by class only
            session.rollback()
            logger.error("delivery_internal_error", extra={"delivery": redact_id(delivery_id)})
            outcome = _record(session, delivery_id, token, _retry_values(
                session, delivery_id, SendResult(Outcome.RETRY, ErrorClass.INTERNAL), now))
            session.commit()
            stats.retried += 1
        if outcome is False:
            stats.lost_lease += 1
    return stats


def _retry_values(session: Session, delivery_id, result: SendResult, now: datetime) -> dict:
    delivery = session.get(NotificationDelivery, delivery_id)
    event = session.get(NotificationEvent, delivery.event_id)
    common = {"error_class": result.error_class.value if result.error_class else None,
              "provider_status": result.provider_status,
              "provider_reason": result.provider_reason, "updated_at": now}
    if delivery.attempts >= delivery.max_attempts:
        return {**common, "status": "failed_permanent",
                "error_class": ErrorClass.MAX_ATTEMPTS.value}
    when = next_attempt(delivery.attempts, now, retry_after=result.retry_after)
    if when > _utc(event.observed_at) + EVENT_TTL:
        return {**common, "status": "suppressed",
                "suppressed_reason": SuppressedReason.EVENT_EXPIRED.value}
    return {**common, "status": "retry", "next_attempt_at": when}


def _deliver_one(session: Session, provider: PushProvider, delivery_id, token, now: datetime,
                 stats: DeliverStats):
    delivery = session.get(NotificationDelivery, delivery_id)
    checked = _recheck(session, delivery, now)
    if isinstance(checked, SuppressedReason):
        ok = _record(session, delivery_id, token, {"status": "suppressed",
                                                   "suppressed_reason": checked.value,
                                                   "updated_at": now})
        session.commit()
        stats.suppressed += ok
        return ok
    facts, endpoint = checked
    channel = delivery.channel
    live_activity_id = delivery.live_activity_id
    try:
        payload = (live_activity_payload(facts) if channel == Channel.APNS_LIVE_ACTIVITY.value
                   else alert_payload(facts))
    except PayloadTooLarge:
        ok = _record(session, delivery_id, token, {
            "status": "failed_permanent", "error_class": ErrorClass.PAYLOAD_TOO_LARGE.value,
            "updated_at": now})
        session.commit()
        stats.failed += ok
        return ok
    request = PushRequest(delivery_id=delivery_id, channel=channel,
                          environment=endpoint.environment, payload=payload,
                          expiration=facts.observed_at + EVENT_TTL,
                          collapse_id=facts.event_id, token=endpoint.token)
    endpoint_id = endpoint.id
    session.commit()                              # NO transaction is open during the send
    result = provider.send(request)
    if result.error_class:
        stats.by_error[result.error_class.value] = stats.by_error.get(
            result.error_class.value, 0) + 1
    if result.outcome is Outcome.ACCEPTED:
        ok = _record(session, delivery_id, token, {
            "status": "delivered", "delivered_at": now, "error_class": None,
            "provider_status": result.provider_status, "provider_reason": None,
            "updated_at": now})
        if ok and live_activity_id is not None:
            # The `end` update was accepted: the activity's job is done.
            activity = session.get(LiveActivity, live_activity_id)
            if activity is not None:
                _close_activities(session, [activity], "opening_delivered", now)
        stats.delivered += ok
    elif result.outcome is Outcome.ENDPOINT_INVALID:
        ok = _record(session, delivery_id, token, {
            "status": "failed_permanent", "error_class": result.error_class.value,
            "provider_status": result.provider_status,
            "provider_reason": result.provider_reason, "updated_at": now})
        if ok:
            invalid = session.get(DeliveryEndpoint, endpoint_id)
            _revoke(invalid, "provider_invalid", now)
            _suppress_pending_for(session, [endpoint_id], SuppressedReason.ENDPOINT_REVOKED, now)
            for activity in session.scalars(select(LiveActivity).where(
                    LiveActivity.endpoint_id == endpoint_id, LiveActivity.state == "active")):
                _end_activity(activity, "provider_invalid", now)
            stats.endpoints_revoked += 1
        stats.failed += ok
    elif result.outcome is Outcome.PERMANENT:
        ok = _record(session, delivery_id, token, {
            "status": "failed_permanent", "error_class": result.error_class.value,
            "provider_status": result.provider_status,
            "provider_reason": result.provider_reason, "updated_at": now})
        stats.failed += ok
    else:
        values = _retry_values(session, delivery_id, result, now)
        ok = _record(session, delivery_id, token, values)
        if values["status"] == "retry":
            stats.retried += ok
        elif values["status"] == "suppressed":
            stats.suppressed += ok
        else:
            stats.failed += ok
    session.commit()
    return ok


# ==========================================================================
# the scheduler lease: one worker polls one term at a time
# ==========================================================================


def schedules(session: Session, *, active_only: bool = True):
    q = select(AvailabilityPollSchedule)
    if active_only:
        q = q.where(AvailabilityPollSchedule.active.is_(True))
    return session.scalars(q.order_by(AvailabilityPollSchedule.term_code)).all()


def consecutive_fetch_failures(session: Session, term_code: str, *, limit: int = 20) -> int:
    verdicts = session.scalars(select(AvailabilityRefresh.verdict).where(
        AvailabilityRefresh.term_code == term_code)
        .order_by(AvailabilityRefresh.observed_at.desc(), AvailabilityRefresh.id).limit(limit))
    count = 0
    for verdict in verdicts:
        if verdict != "rejected_fetch_failed":
            break
        count += 1
    return count


def next_due(session: Session, schedule: AvailabilityPollSchedule,
             now: datetime | None = None, windows=None) -> datetime | None:
    """When the term should next be polled, from the REFRESH LOG (so restarts
    and manual runs never double-poll). Fetch failures back off exponentially,
    capped; other rejections keep the normal cadence (data, not transport).

    Phase 7.3: the cadence is 90 s inside the term's Rutgers registration
    window and 30 minutes outside (app.core.registration_windows) - still ONE
    openSections request per term per poll. Backoff never makes a failing
    term poll FASTER than its normal cadence."""
    last = session.scalar(select(func.max(AvailabilityRefresh.observed_at)).where(
        AvailabilityRefresh.term_code == schedule.term_code))
    if last is None:
        return None
    moment = now or datetime.now(UTC)
    interval = poll_interval(schedule.term_code, schedule.interval_seconds, moment,
                             windows).total_seconds()
    k = consecutive_fetch_failures(session, schedule.term_code)
    seconds = min(max(schedule.backoff_cap_seconds, interval), interval * (2 ** k))
    return _utc(last) + timedelta(seconds=seconds)


def claim_term(session: Session, term_code: str, *, owner: str, now: datetime,
               lease: timedelta) -> AvailabilityPollSchedule | None:
    """Take the term's lease if it is due and free; commit; return it or None.
    The due check is repeated AFTER taking the lease, so a worker that read a
    stale "due" never re-polls a term another worker just refreshed."""
    result = session.execute(_update(AvailabilityPollSchedule).where(
        AvailabilityPollSchedule.term_code == term_code, AvailabilityPollSchedule.active.is_(True),
        or_(AvailabilityPollSchedule.lease_until.is_(None),
            AvailabilityPollSchedule.lease_until < now))
        .values(lease_owner=owner, lease_until=now + lease, last_started_at=now, updated_at=now))
    session.commit()
    if not result.rowcount:
        return None
    schedule = session.get(AvailabilityPollSchedule, term_code)
    session.refresh(schedule)
    due = next_due(session, schedule, now)
    if due is not None and now < due:
        release_term(session, term_code, owner=owner, now=now, outcome=None)
        return None
    return schedule


def holds_lease(session: Session, term_code: str, *, owner: str, now: datetime) -> bool:
    """The fence: still ours and unexpired (locks the row on PostgreSQL)."""
    q = select(AvailabilityPollSchedule).where(
        AvailabilityPollSchedule.term_code == term_code,
        AvailabilityPollSchedule.lease_owner == owner, AvailabilityPollSchedule.lease_until >= now)
    if session.get_bind().dialect.name == "postgresql":
        q = q.with_for_update()
    return session.scalar(q) is not None


def release_term(session: Session, term_code: str, *, owner: str, now: datetime,
                 outcome: str | None, duration_ms: int | None = None) -> None:
    values = {"lease_owner": None, "lease_until": None, "updated_at": now}
    if outcome is not None:
        values.update(last_finished_at=now, last_outcome=outcome[:40],
                      last_duration_ms=duration_ms)
    session.execute(_update(AvailabilityPollSchedule).where(
        AvailabilityPollSchedule.term_code == term_code,
        AvailabilityPollSchedule.lease_owner == owner).values(**values))
    session.commit()


__all__ = ["DeliverStats", "FanoutStats", "Invalid", "LiveError", "NotFound", "claim",
           "claim_term", "consecutive_fetch_failures", "create_inbox_items", "deliver_due",
           "end_activities_for_watches", "end_live_activity", "expire_live_activities",
           "fan_out", "holds_lease", "list_endpoints", "list_live_activities",
           "list_notifications", "mark_all_read", "mark_read", "next_due",
           "register_endpoint", "register_live_activity", "release_term", "revoke_endpoint",
           "rotate_live_activity_token", "schedules", "unread_count"]
