"""CoursePilot Live API (Phase 6.9): inbox, push endpoints, Live Activities, health.

```
GET    /api/v1/notifications                  ?limit=&cursor=      newest first
GET    /api/v1/notifications/unread-count
POST   /api/v1/notifications/{id}/read                             idempotent
POST   /api/v1/notifications/read-all         {"up_to": null}
POST   /api/v1/push-endpoints                 {"kind","environment","token",
                                               "replaces_endpoint_id"}
GET    /api/v1/push-endpoints
DELETE /api/v1/push-endpoints/{id}
POST   /api/v1/live-activities                {"watch_id","client_activity_id",
                                               "push_token","environment"}
GET    /api/v1/live-activities
PUT    /api/v1/live-activities/{id}/token     {"push_token","environment"}     ActivityKit rotation
POST   /api/v1/live-activities/{id}/end
GET    /api/v1/admin/live/health              administrators only
```

An opening notification reports an OBSERVED Rutgers availability change.
It does not guarantee eligibility, schedule compatibility, or successful
registration.

Ownership comes from the credential only (`extra="forbid"` turns any
owner field into a 422); another account's row is a 404, exactly like a
missing one. Tokens are accepted, never returned and never logged: responses
and logs carry a 12-character fingerprint.
"""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.security import Principal, enforce_push_registration_limit, enforce_request_rate_limit
from app.api.v1.routes.admin import require_admin_account
from app.api.v1.routes.watches import EventView, _event_view
from app.db.session import get_sync_sessionmaker
from app.models.availability import AvailabilityRefresh
from app.models.live import (
    DeliveryEndpoint,
    LiveActivity,
    Notification,
    NotificationDelivery,
)
from app.models.monitoring import NotificationEvent
from app.services import live as svc
from app.services.monitoring import pending_openings

logger = logging.getLogger(__name__)

inbox = APIRouter(prefix="/notifications", tags=["notifications"])
endpoints = APIRouter(prefix="/push-endpoints", tags=["push"])
activities = APIRouter(prefix="/live-activities", tags=["push"])
admin = APIRouter(prefix="/admin/live", tags=["admin"])

_TOKEN = Field(min_length=32, max_length=512, repr=False)


def _now() -> datetime:
    return datetime.now(UTC)


async def _run(work, event: str, principal: Principal):
    try:
        return await run_in_threadpool(work)
    except svc.NotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except svc.Invalid as exc:
        logger.info(event, extra={"principal": principal.redacted(), "outcome": "invalid"})
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=str(exc)) from None
    except HTTPException:
        raise
    except Exception:
        # No traceback: request bodies here carry push tokens.
        logger.error(f"{event}_failed", extra={"principal": principal.redacted()})
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Unable to process the request.") from None


# ==========================================================================
# inbox
# ==========================================================================


class NotificationView(BaseModel):
    notification_id: uuid.UUID
    read: bool
    read_at: datetime | None = None
    created_at: datetime
    event: EventView


class NotificationPage(BaseModel):
    notifications: list[NotificationView]
    next_cursor: str | None = None          # opaque; pass back as ?cursor=


class UnreadCount(BaseModel):
    unread: int


class ReadAll(BaseModel):
    up_to: datetime | None = None           # default: everything created until now
    model_config = {"extra": "forbid"}


class ReadAllResult(BaseModel):
    marked: int


def _cursor(item: Notification) -> str:
    raw = f"{item.created_at.isoformat()}|{item.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _parse_cursor(cursor: str | None):
    if not cursor:
        return None
    try:
        created, ident = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(created), uuid.UUID(ident)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail="Invalid cursor.") from None


def _note(item: Notification, event) -> NotificationView:
    return NotificationView(notification_id=item.id, read=item.read_at is not None,
                            read_at=item.read_at, created_at=item.created_at,
                            event=_event_view(event))


@inbox.get("", response_model=NotificationPage, summary="My notifications, newest first")
async def list_inbox(limit: int = Query(default=20, ge=1, le=50), cursor: str | None = None,
                     principal: Principal = Depends(enforce_request_rate_limit)):
    before = _parse_cursor(cursor)

    def work():
        with get_sync_sessionmaker()() as s:
            rows = svc.list_notifications(s, principal.account_id, limit=limit, before=before)
            page = [_note(n, e) for n, e in rows]
            return NotificationPage(notifications=page,
                                    next_cursor=_cursor(rows[-1][0]) if len(rows) == limit
                                    else None)
    return await _run(work, "inbox_list", principal)


@inbox.get("/unread-count", response_model=UnreadCount)
async def unread(principal: Principal = Depends(enforce_request_rate_limit)):
    def work():
        with get_sync_sessionmaker()() as s:
            return UnreadCount(unread=svc.unread_count(s, principal.account_id))
    return await _run(work, "inbox_unread", principal)


@inbox.post("/{notification_id}/read", response_model=NotificationView)
async def read_one(notification_id: uuid.UUID,
                   principal: Principal = Depends(enforce_request_rate_limit)):
    def work():
        with get_sync_sessionmaker()() as s:
            item = svc.mark_read(s, principal.account_id, notification_id, now=_now())
            s.commit()
            event = s.get(NotificationEvent, item.event_id)
            return _note(item, event)
    return await _run(work, "inbox_read", principal)


@inbox.post("/read-all", response_model=ReadAllResult)
async def read_all(body: ReadAll | None = None,
                   principal: Principal = Depends(enforce_request_rate_limit)):
    def work():
        with get_sync_sessionmaker()() as s:
            marked = svc.mark_all_read(s, principal.account_id, now=_now(),
                                       up_to=body.up_to if body else None)
            s.commit()
            return ReadAllResult(marked=marked)
    return await _run(work, "inbox_read_all", principal)


# ==========================================================================
# push endpoints
# ==========================================================================


class EndpointRequest(BaseModel):
    kind: Literal["apns_device", "apns_push_to_start"] = "apns_device"
    environment: Literal["sandbox", "production"]
    token: str = _TOKEN
    replaces_endpoint_id: uuid.UUID | None = None
    model_config = {"extra": "forbid"}


class EndpointView(BaseModel):
    endpoint_id: uuid.UUID
    kind: str
    environment: str
    fingerprint: str                         # never the token
    created_at: datetime
    last_seen_at: datetime | None = None


class EndpointList(BaseModel):
    endpoints: list[EndpointView]


def _endpoint(e: DeliveryEndpoint) -> EndpointView:
    return EndpointView(endpoint_id=e.id, kind=e.kind, environment=e.environment,
                        fingerprint=e.token_fingerprint, created_at=e.created_at,
                        last_seen_at=e.last_seen_at)


@endpoints.post("", response_model=EndpointView, summary="Register or rotate a push token")
async def register_endpoint(body: EndpointRequest,
                            principal: Principal = Depends(enforce_push_registration_limit)):
    def work():
        with get_sync_sessionmaker()() as s:
            e = svc.register_endpoint(s, principal.account_id, kind=body.kind,
                                      environment=body.environment, token=body.token,
                                      now=_now(), replaces_endpoint_id=body.replaces_endpoint_id)
            s.commit()
            return _endpoint(e)
    out = await _run(work, "push_endpoint_register", principal)
    logger.info("push_endpoint_register", extra={"principal": principal.redacted(),
                                                 "fingerprint": out.fingerprint})
    return out


@endpoints.get("", response_model=EndpointList)
async def list_endpoints(principal: Principal = Depends(enforce_request_rate_limit)):
    def work():
        with get_sync_sessionmaker()() as s:
            return EndpointList(endpoints=[_endpoint(e) for e in svc.list_endpoints(
                s, principal.account_id)])
    return await _run(work, "push_endpoint_list", principal)


@endpoints.delete("/{endpoint_id}", response_model=EndpointView, summary="Revoke (idempotent)")
async def revoke_endpoint(endpoint_id: uuid.UUID,
                          principal: Principal = Depends(enforce_request_rate_limit)):
    def work():
        with get_sync_sessionmaker()() as s:
            e = svc.revoke_endpoint(s, principal.account_id, endpoint_id, now=_now())
            s.commit()
            return _endpoint(e)
    return await _run(work, "push_endpoint_revoke", principal)


# ==========================================================================
# Live Activities (ActivityKit)
# ==========================================================================


class ActivityRequest(BaseModel):
    watch_id: uuid.UUID
    client_activity_id: str = Field(min_length=1, max_length=128)
    push_token: str = _TOKEN
    environment: Literal["sandbox", "production"]
    model_config = {"extra": "forbid"}


class TokenRotation(BaseModel):
    push_token: str = _TOKEN
    environment: Literal["sandbox", "production"]
    model_config = {"extra": "forbid"}


class ActivityView(BaseModel):
    live_activity_id: uuid.UUID
    watch_id: uuid.UUID
    client_activity_id: str
    state: str                               # active | ended | expired
    started_at: datetime
    expires_at: datetime                     # Apple ends an activity after 8 hours
    ended_at: datetime | None = None
    ended_reason: str | None = None
    token_fingerprint: str | None = None


class ActivityList(BaseModel):
    live_activities: list[ActivityView]


def _activity(s, a: LiveActivity) -> ActivityView:
    e = s.get(DeliveryEndpoint, a.endpoint_id) if a.endpoint_id else None
    return ActivityView(live_activity_id=a.id, watch_id=a.watch_id,
                        client_activity_id=a.client_activity_id, state=a.state,
                        started_at=a.started_at, expires_at=a.expires_at, ended_at=a.ended_at,
                        ended_reason=a.ended_reason,
                        token_fingerprint=e.token_fingerprint if e else None)


@activities.post("", response_model=ActivityView, summary="Register a started Live Activity")
async def register_activity(body: ActivityRequest,
                            principal: Principal = Depends(enforce_push_registration_limit)):
    def work():
        with get_sync_sessionmaker()() as s:
            a = svc.register_live_activity(
                s, principal.account_id, watch_id=body.watch_id,
                client_activity_id=body.client_activity_id, push_token=body.push_token,
                environment=body.environment, now=_now())
            s.commit()
            return _activity(s, a)
    return await _run(work, "live_activity_register", principal)


@activities.get("", response_model=ActivityList)
async def list_activities(principal: Principal = Depends(enforce_request_rate_limit)):
    def work():
        with get_sync_sessionmaker()() as s:
            return ActivityList(live_activities=[_activity(s, a) for a in
                                                 svc.list_live_activities(s, principal.account_id)])
    return await _run(work, "live_activity_list", principal)


@activities.put("/{activity_id}/token", response_model=ActivityView,
                summary="ActivityKit pushTokenUpdates delivered a new token")
async def rotate_token(activity_id: uuid.UUID, body: TokenRotation,
                       principal: Principal = Depends(enforce_push_registration_limit)):
    def work():
        with get_sync_sessionmaker()() as s:
            a = svc.rotate_live_activity_token(s, principal.account_id, activity_id,
                                               push_token=body.push_token,
                                               environment=body.environment, now=_now())
            s.commit()
            return _activity(s, a)
    return await _run(work, "live_activity_rotate", principal)


@activities.post("/{activity_id}/end", response_model=ActivityView)
async def end_activity(activity_id: uuid.UUID,
                       principal: Principal = Depends(enforce_request_rate_limit)):
    def work():
        with get_sync_sessionmaker()() as s:
            a = svc.end_live_activity(s, principal.account_id, activity_id, now=_now())
            s.commit()
            return _activity(s, a)
    return await _run(work, "live_activity_end", principal)


# ==========================================================================
# operator health - from the database, so it sees every worker process
# ==========================================================================


class TermHealth(BaseModel):
    term_code: str
    active: bool
    interval_seconds: int
    last_outcome: str | None = None
    last_finished_at: datetime | None = None
    last_duration_ms: int | None = None
    last_accepted_at: datetime | None = None
    consecutive_fetch_failures: int
    lease_held: bool
    healthy: bool


class LiveHealth(BaseModel):
    terms: list[TermHealth]
    scheduler_healthy: bool
    pending_openings: int
    pending_fanout: int
    deliveries: dict[str, int]
    endpoints_invalidated: int
    active_live_activities: int


def _health(s, now: datetime) -> LiveHealth:
    terms = []
    for sch in svc.schedules(s, active_only=False):
        accepted = s.scalar(select(func.max(AvailabilityRefresh.observed_at)).where(
            AvailabilityRefresh.term_code == sch.term_code,
            AvailabilityRefresh.verdict == "accepted"))
        finished = sch.last_finished_at
        if finished is not None and finished.tzinfo is None:
            finished = finished.replace(tzinfo=UTC)
        healthy = bool(sch.active and finished is not None
                       and (now - finished).total_seconds() <= 3 * sch.backoff_cap_seconds
                       and not (sch.last_outcome or "").startswith("error"))
        terms.append(TermHealth(
            term_code=sch.term_code, active=sch.active, interval_seconds=sch.interval_seconds,
            last_outcome=sch.last_outcome, last_finished_at=sch.last_finished_at,
            last_duration_ms=sch.last_duration_ms, last_accepted_at=accepted,
            consecutive_fetch_failures=svc.consecutive_fetch_failures(s, sch.term_code),
            lease_held=sch.lease_owner is not None, healthy=healthy))
    counts = dict(s.execute(select(NotificationDelivery.status, func.count())
                            .group_by(NotificationDelivery.status)).all())
    return LiveHealth(
        terms=terms, scheduler_healthy=bool(terms) and all(t.healthy for t in terms if t.active),
        pending_openings=pending_openings(s),
        pending_fanout=s.scalar(select(func.count()).select_from(Notification).where(
            Notification.fanned_out_at.is_(None))) or 0,
        deliveries={k: int(v) for k, v in sorted(counts.items())},
        endpoints_invalidated=s.scalar(select(func.count()).select_from(DeliveryEndpoint).where(
            DeliveryEndpoint.revoked_reason == "provider_invalid")) or 0,
        active_live_activities=s.scalar(select(func.count()).select_from(LiveActivity).where(
            LiveActivity.state == "active")) or 0)


@admin.get("/health", response_model=LiveHealth, summary="Scheduler / outbox health (admins)")
async def live_health(principal: Principal = Depends(require_admin_account)):
    def work():
        with get_sync_sessionmaker()() as s:
            return _health(s, _now())
    return await _run(work, "live_health", principal)


routers = (inbox, endpoints, activities, admin)
