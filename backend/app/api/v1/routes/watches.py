"""Section watches and notification events for the authenticated account (Phase 6.8).

```
POST   /api/v1/watches                 {"term": "20269", "index_number": "13384"}
GET    /api/v1/watches                 ?include_inactive=false
DELETE /api/v1/watches/{watch_id}
GET    /api/v1/watches/events          ?limit=50
```

A watch asks CoursePilot's SERVER to watch one term-scoped section; it keeps
working with the browser closed or the app terminated, because the single
global availability refresh - one Rutgers request per term, whatever the
number of watches - feeds the event processor, not any client. No request
here fetches Rutgers or processes transitions.

Ownership comes from the credential only: no body or query field names a
user (`extra="forbid"` makes `account_id`/`student_id` a 422), and another
account's watch is a 404, indistinguishable from a missing one. Logs carry
the redacted principal and counts - never a term, index or course, which
together with a user would reveal their academic intent.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.security import Principal, enforce_request_rate_limit
from app.db.session import get_sync_sessionmaker
from app.domain.availability import Freshness
from app.domain.monitoring import MAX_EVENTS_PAGE, WatchStatus
from app.services.monitoring import (
    TargetNotFound,
    TargetRemoved,
    TooManyWatches,
    WatchError,
    create_watch,
    deactivate_watch,
    describe_watches,
    list_events,
    list_watches,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/watches", tags=["watches"])


class WatchRequest(BaseModel):
    term: str = Field(pattern=r"^\d{4}[0179]$")
    index_number: str = Field(pattern=r"^\d{5}$")

    model_config = {"extra": "forbid"}


class AvailabilityNow(BaseModel):
    """The target's availability when this response was built - render as
    "<state>, current as of <observed_at>" only when freshness is current."""

    state: str                              # open | closed | unknown
    freshness: str                          # current | stale | unobserved
    observed_at: datetime | None = None
    source: str | None = None


class WatchView(BaseModel):
    watch_id: uuid.UUID
    status: WatchStatus
    term_code: str
    index_number: str
    course_string: str | None = None
    supplement_code: str | None = None      # "LB" for a lab registration record
    section_number: str | None = None
    #: The section is no longer in Rutgers' schedule (or no longer in CoursePilot).
    target_removed: bool = False
    #: What the watch waits for - always the NEXT observed closed -> open.
    notifies_on: str = "closed_to_open"
    created_at: datetime
    deactivated_at: datetime | None = None
    deactivation_reason: str | None = None  # user_cancelled | term_ended | target_removed
    current_availability: AvailabilityNow | None = None


class WatchCreated(BaseModel):
    created: bool                           # false: an identical active watch already existed
    watch: WatchView


class WatchList(BaseModel):
    watches: list[WatchView]


class RequiredComponent(BaseModel):
    """The other required registration record(s) of the course (e.g. the LB lab
    of a lecture), when the event was created. state: open only if at least
    one of their indexes was open."""

    state: str
    open_indexes: int
    total_indexes: int
    as_of: datetime | None = None


class EventView(BaseModel):
    event_id: uuid.UUID                     # also the future delivery idempotency key
    kind: str                               # section_opened
    watch_id: uuid.UUID
    term_code: str
    index_number: str
    course_string: str
    supplement_code: str
    section_number: str
    from_status: str                        # closed
    to_status: str                          # open
    #: When CoursePilot observed the change - "opened at", never "open now".
    observed_at: datetime
    created_at: datetime
    required_component: RequiredComponent | None = None


class EventList(BaseModel):
    events: list[EventView]


def _now() -> datetime:
    return datetime.now(UTC)


def _view(watch, described) -> WatchView:
    target, resolved = described
    now = None
    if resolved is not None:
        now = AvailabilityNow(state=resolved.status.value, freshness=resolved.freshness.value,
                              observed_at=resolved.observed_at, source=resolved.source)
    elif not target.removed:
        now = AvailabilityNow(state="unknown", freshness=Freshness.UNOBSERVED.value)
    return WatchView(
        watch_id=watch.id,
        status=WatchStatus.ACTIVE if watch.deactivated_at is None else WatchStatus.INACTIVE,
        term_code=watch.term_code, index_number=watch.index_number,
        course_string=target.course_string, supplement_code=target.supplement_code,
        section_number=target.section_number, target_removed=target.removed,
        created_at=watch.created_at, deactivated_at=watch.deactivated_at,
        deactivation_reason=watch.deactivation_reason, current_availability=now)


def _event_view(e) -> EventView:
    component = None
    if e.required_component_state is not None:
        component = RequiredComponent(state=e.required_component_state,
                                      open_indexes=e.required_components_open,
                                      total_indexes=e.required_components_total,
                                      as_of=e.context_as_of)
    return EventView(event_id=e.id, kind=e.kind, watch_id=e.watch_id, term_code=e.term_code,
                     index_number=e.index_number, course_string=e.course_string,
                     supplement_code=e.supplement_code, section_number=e.section_number,
                     from_status=e.from_status, to_status=e.to_status,
                     observed_at=e.observed_at, created_at=e.created_at,
                     required_component=component)


#: 404 not_found / 422 validation_error in the repo's error taxonomy. Not 409:
#: there 409 means unlinked_account.
_STATUS = {TargetNotFound: status.HTTP_404_NOT_FOUND,
           TargetRemoved: status.HTTP_422_UNPROCESSABLE_CONTENT,
           TooManyWatches: status.HTTP_422_UNPROCESSABLE_CONTENT}


async def _run(work, event: str, principal: Principal):
    try:
        return await run_in_threadpool(work)
    except WatchError as exc:
        logger.info(event, extra={"principal": principal.redacted(), "outcome": exc.code})
        raise HTTPException(status_code=_STATUS.get(type(exc), 422), detail=str(exc)) from None
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"{event}_failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Unable to process the watch request.") from None


@router.post("", response_model=WatchCreated, summary="Watch one term-scoped section")
async def create(body: WatchRequest,
                 principal: Principal = Depends(enforce_request_rate_limit)) -> WatchCreated:
    def work():
        now = _now()
        with get_sync_sessionmaker()() as session:
            watch, created = create_watch(session, principal.account_id, term_code=body.term,
                                          index_number=body.index_number, now=now)
            session.commit()
            return WatchCreated(created=created,
                                watch=_view(watch, describe_watches(session, [watch],
                                                                    now)[watch.id]))
    out = await _run(work, "watch_create", principal)
    logger.info("watch_create", extra={"principal": principal.redacted(),
                                       "outcome": "created" if out.created else "existing"})
    return out


@router.get("", response_model=WatchList, summary="My watches")
async def list_mine(include_inactive: bool = Query(default=False),
                    principal: Principal = Depends(enforce_request_rate_limit)) -> WatchList:
    def work():
        now = _now()
        with get_sync_sessionmaker()() as session:
            watches = list_watches(session, principal.account_id,
                                   include_inactive=include_inactive)
            described = describe_watches(session, watches, now)
            return WatchList(watches=[_view(w, described[w.id]) for w in watches])
    out = await _run(work, "watch_list", principal)
    logger.info("watch_list", extra={"principal": principal.redacted(),
                                     "watches": len(out.watches)})
    return out


@router.get("/events", response_model=EventList, summary="My notification events")
async def events(limit: int = Query(default=50, ge=1, le=MAX_EVENTS_PAGE),
                 principal: Principal = Depends(enforce_request_rate_limit)) -> EventList:
    def work():
        with get_sync_sessionmaker()() as session:
            return EventList(events=[_event_view(e) for e in list_events(
                session, principal.account_id, limit=limit)])
    out = await _run(work, "watch_events", principal)
    logger.info("watch_events", extra={"principal": principal.redacted(),
                                       "events": len(out.events)})
    return out


@router.delete("/{watch_id}", response_model=WatchView, summary="Stop watching (idempotent)")
async def deactivate(watch_id: uuid.UUID,
                     principal: Principal = Depends(enforce_request_rate_limit)) -> WatchView:
    def work():
        now = _now()
        with get_sync_sessionmaker()() as session:
            watch = deactivate_watch(session, principal.account_id, watch_id, now=now)
            if watch is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                    detail="No such watch.")
            session.commit()
            return _view(watch, describe_watches(session, [watch], now)[watch.id])
    out = await _run(work, "watch_deactivate", principal)
    logger.info("watch_deactivate", extra={"principal": principal.redacted()})
    return out
