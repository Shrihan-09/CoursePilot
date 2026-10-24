"""CoursePilot Live on real Rutgers data (Phase 6.9) - SQLite.

Structure: the real Fall 2026 component fixtures (01:750:193/194 + "LB",
227, 229, ...) through the production pipelines. First availability: the
real 2026-10-14 openSections payload. Every later opening is a SYNTHETIC
transition applied to a real section (Rutgers' real polls showed none).

Pushes go to a deterministic FakeProvider: nothing reaches Apple and no
device is involved. Real PostgreSQL concurrency is in test_live_postgres.py.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import UTC, timedelta

import httpx
import pytest
from app.domain.live import EVENT_TTL, MAX_ATTEMPTS, ErrorClass, Outcome, SendResult
from app.models import UserAccount
from app.models.availability import AvailabilityRefresh
from app.models.live import (
    AvailabilityPollSchedule,
    DeliveryEndpoint,
    LiveActivity,
    Notification,
    NotificationDelivery,
)
from app.models.monitoring import NotificationEvent
from app.services import live as svc
from app.services.monitoring import create_watch, deactivate_watch, process_openings
from app.services.push_providers import FakeProvider
from sqlalchemy import func, select

from coursepilot_ingestion.live_worker import LiveWorker

from .conftest import FIXTURE_DIR
from .test_live_availability import FALL26, T0, _payload, _refresh, world  # noqa: F401

PAYLOAD = FIXTURE_DIR / "open_sections_fall2026_20261014T022951Z.json"
BEFORE = T0 - timedelta(hours=1)
T1 = T0 + timedelta(minutes=2)
SOON = T1 + timedelta(seconds=30)


def _token(n: int) -> str:
    return hashlib.sha256(f"device-{n}".encode()).hexdigest()       # 64 hex, SYNTHETIC


def _account(s) -> uuid.UUID:
    a = UserAccount(identity_provider="dev", external_subject=f"live-{uuid.uuid4().hex}")
    s.add(a)
    s.flush()
    return a.id


def _watch(s, account, index, at=BEFORE):
    w, _ = create_watch(s, account, term_code=FALL26, index_number=index, now=at)
    s.commit()
    return w


def _device(s, account, n, at=BEFORE):
    e = svc.register_endpoint(s, account, kind="apns_device", environment="sandbox",
                              token=_token(n), now=at)
    s.commit()
    return e


def _activity(s, account, watch, n, at=BEFORE):
    a = svc.register_live_activity(s, account, watch_id=watch.id,
                                   client_activity_id=f"act-{n}", push_token=_token(1000 + n),
                                   environment="sandbox", now=at)
    s.commit()
    return a


def _open(s, *indexes, at=T1, drop=()):
    entries = [i for i in _payload() if i not in drop] + list(indexes)
    return _refresh(s, entries, at=at)


def _pipeline(s, provider, now=SOON):
    process_openings(s, now=now)
    svc.fan_out(s, now=now)
    return svc.deliver_due(s, provider, now=now)


def _deliveries(s, account=None):
    q = select(NotificationDelivery)
    if account is not None:
        q = q.where(NotificationDelivery.user_account_id == account)
    return s.scalars(q.order_by(NotificationDelivery.channel, NotificationDelivery.id)).all()


@pytest.fixture(autouse=True)
def _inside_a_registration_window(monkeypatch):
    """These tests are about the 90 s in-window cadence and its backoff (Phase
    7.3 made the outside-window cadence 30 minutes - tested separately)."""
    from app.core import registration_windows as rw

    monkeypatch.setattr(rw, "REGISTRATION_WINDOWS", (rw.RegistrationWindow(
        FALL26, T0 - timedelta(days=1), T0 + timedelta(days=60), "SYNTHETIC test window",
        "synthetic://test"),))


@pytest.fixture
def armed(world):  # noqa: F811
    assert _refresh(world).verdict == "accepted"             # real first observation at T0
    return world


# ==========================================================================
# 6.7 -> 6.8 -> 6.9: one opening, every surface
# ==========================================================================


def test_an_opening_reaches_the_inbox_the_live_activity_and_suppresses_the_double_alert(
        armed) -> None:
    """Real lab 01:750:194 LB 13385 (closed at T0); SYNTHETIC opening at T1."""
    account = _account(armed)
    watch = _watch(armed, account, "13385")
    _device(armed, account, 1)
    activity = _activity(armed, account, watch, 1)
    _open(armed, "13385")
    provider = FakeProvider()
    stats = _pipeline(armed, provider)
    [item] = armed.scalars(select(Notification).where(Notification.user_account_id == account))
    assert item.read_at is None and item.fanned_out_at is not None
    rows = {d.channel: d for d in _deliveries(armed, account)}
    assert rows["apns_live_activity"].status == "delivered"
    assert (rows["apns_alert"].status, rows["apns_alert"].suppressed_reason) == (
        "suppressed", "live_activity_alerted")                # Apple HIG: one alert
    assert stats.delivered == 1 and len(provider.sent) == 1
    [sent] = provider.sent
    aps = json.loads(sent.payload)["aps"]
    assert aps["event"] == "end" and aps["content-state"]["index"] == "13385"
    # real linked component: the lab's lecture record, every lecture closed
    assert aps["content-state"]["required_component"]["total"] == 8
    armed.refresh(activity)
    assert (activity.state, activity.ended_reason) == ("ended", "opening_delivered")


def test_without_a_live_activity_the_normal_push_is_sent(armed) -> None:
    account = _account(armed)
    _watch(armed, account, "13385")
    _device(armed, account, 1)
    _device(armed, account, 2)
    _open(armed, "13385")
    provider = FakeProvider()
    _pipeline(armed, provider)
    assert sorted(d.status for d in _deliveries(armed, account)) == ["delivered", "delivered"]
    assert {json.loads(r.payload)["aps"]["alert"]["title-loc-key"] for r in provider.sent} == {
        "CP_SECTION_OPENED_TITLE"}


def test_a_course_without_components_gets_no_lab_field(armed) -> None:
    """Real 01:750:229 (an ACADEMIC co-requisite of 227, not a registration
    component): the payload has no component field at all."""
    account = _account(armed)
    from app.models import Course, CourseOffering, CourseSection
    closed_229 = armed.scalar(
        select(CourseSection.index_number)
        .join(CourseOffering, CourseOffering.id == CourseSection.offering_id)
        .join(Course, Course.id == CourseOffering.course_id)
        .where(Course.course_string == "01:750:229", CourseSection.term_code == FALL26,
               CourseSection.index_number.notin_(["13551", "13564"]))
        .order_by(CourseSection.index_number).limit(1))
    _watch(armed, account, closed_229)
    _device(armed, account, 1)
    _open(armed, closed_229)
    provider = FakeProvider()
    _pipeline(armed, provider)
    [sent] = provider.sent
    state = json.loads(sent.payload)["coursepilot"]
    assert state["course"] == "01:750:229"
    assert "required_component" not in state and "supplement" not in state


def test_processing_is_automatic_and_needs_no_client(armed, tmp_path) -> None:
    """The worker alone: poll (ONE Rutgers request) -> event -> inbox -> push."""
    account = _account(armed)
    _watch(armed, account, "13385")
    _device(armed, account, 1)
    armed.add(AvailabilityPollSchedule(term_code=FALL26, interval_seconds=90,
                                       backoff_cap_seconds=900))
    armed.commit()
    body = json.dumps(_payload() + ["13385"]).encode()
    calls = []

    def rutgers(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=body)

    provider = FakeProvider()
    worker = _worker(armed, tmp_path, provider, rutgers, clock=lambda: T1)
    report = worker.tick()
    assert report.polled == {FALL26: "accepted"} and len(calls) == 1
    assert report.openings == 1 and report.events == 1 and report.delivered == 1
    assert not report.errors


def _worker(s, tmp_path, provider, handler, clock, owner="w1", stop=None):
    bind = s.get_bind()
    from sqlalchemy.orm import sessionmaker
    factory = sessionmaker(bind, expire_on_commit=False)
    return LiveWorker(factory, owner=owner, archive_dir=tmp_path, provider=provider,
                      transport=httpx.MockTransport(handler), clock=clock, stop=stop)


# ==========================================================================
# scheduler
# ==========================================================================


def _schedule(s, term=FALL26, interval=90, active=True):
    s.add(AvailabilityPollSchedule(term_code=term, interval_seconds=interval,
                                   backoff_cap_seconds=900, active=active))
    s.commit()


def test_rutgers_requests_do_not_grow_with_watches_or_endpoints(armed, tmp_path) -> None:
    calls = []

    def rutgers(request):
        calls.append(1)
        return httpx.Response(200, content=PAYLOAD.read_bytes())

    _schedule(armed)
    worker = _worker(armed, tmp_path, FakeProvider(), rutgers, clock=lambda: T1)
    worker.tick()
    for n in range(50):
        account = _account(armed)
        watch = _watch(armed, account, "13385")
        _device(armed, account, n)
        _activity(armed, account, watch, n)
    later = _worker(armed, tmp_path, FakeProvider(), rutgers,
                    clock=lambda: T1 + timedelta(seconds=91))
    later.tick()
    assert len(calls) == 2                                    # one per due refresh, period


def test_a_term_is_polled_only_when_due_and_only_by_one_worker(armed, tmp_path) -> None:
    calls = []

    def rutgers(request):
        calls.append(1)
        return httpx.Response(200, content=PAYLOAD.read_bytes())

    _schedule(armed)
    now = [T1]
    first = _worker(armed, tmp_path, None, rutgers, clock=lambda: now[0], owner="a")
    second = _worker(armed, tmp_path, None, rutgers, clock=lambda: now[0], owner="b")
    first.tick()
    second.tick()                                            # same instant: not due again
    assert len(calls) == 1
    now[0] = T1 + timedelta(seconds=60)
    first.tick()
    assert len(calls) == 1                                   # 60 s < 90 s interval
    now[0] = T1 + timedelta(seconds=91)
    second.tick()
    assert len(calls) == 2


def test_inactive_and_unscheduled_terms_are_never_polled(armed, tmp_path) -> None:
    urls = []

    def rutgers(request):
        urls.append(str(request.url))
        return httpx.Response(200, content=b"[]")

    _schedule(armed, term="20259", active=False)             # Fall 2025: inactive
    _schedule(armed, term="20267")                           # Summer 2026: active
    _worker(armed, tmp_path, None, rutgers, clock=lambda: T1).tick()
    assert len(urls) == 1 and "year=2026&term=7" in urls[0]


def test_fetch_failures_back_off_and_are_reported(armed, tmp_path, caplog) -> None:
    _schedule(armed)
    now = [T1]
    worker = _worker(armed, tmp_path, None, lambda r: httpx.Response(503), clock=lambda: now[0])
    report = worker.tick()
    assert report.polled == {FALL26: "rejected_fetch_failed"}
    sched = armed.get(AvailabilityPollSchedule, FALL26)
    armed.refresh(sched)
    assert sched.last_outcome == "rejected_fetch_failed" and sched.lease_owner is None
    due = [svc.next_due(armed, sched)]
    now[0] = due[0]
    worker.tick()                                            # second failure
    gap_2 = svc.next_due(armed, sched) - now[0]
    assert gap_2 == timedelta(seconds=360)                   # 90 * 2**2
    assert svc.consecutive_fetch_failures(armed, FALL26) == 2
    assert any(r.getMessage() == "availability_refresh" and r.levelname == "WARNING"
               for r in caplog.records)


def test_a_scheduler_failure_is_never_silent(armed, tmp_path, caplog) -> None:
    _schedule(armed)

    def broken(request):
        raise RuntimeError("SYNTHETIC failure inside the transport")

    report = _worker(armed, tmp_path, None, broken, clock=lambda: T1).tick()
    # httpx wraps transport errors only for httpx exceptions: this one escapes
    # fetch, is caught at the worker boundary, logged and counted.
    assert report.polled[FALL26].startswith("error:") and "refresh:20269" in report.errors
    assert any(r.getMessage() == "availability_refresh_failed" for r in caplog.records)
    sched = armed.get(AvailabilityPollSchedule, FALL26)
    armed.refresh(sched)
    assert sched.lease_owner is None and sched.last_outcome.startswith("error")


def test_the_loop_stops_cleanly_and_backs_off(armed, tmp_path) -> None:
    stop = threading.Event()
    worker = _worker(armed, tmp_path, None, lambda r: httpx.Response(200, content=b"[]"),
                     clock=lambda: T1, stop=stop)
    assert worker.run(idle=0.0, max_ticks=3) == 3
    stop.set()
    assert worker.run(idle=0.0) == 0                          # already stopped: no tick


# ==========================================================================
# inbox
# ==========================================================================


def _event_row(s, event_id) -> str:
    table = NotificationEvent.__table__
    return repr(tuple(s.execute(select(table).where(table.c.id == event_id)).one()))


def test_reading_never_touches_the_factual_event(armed) -> None:
    account = _account(armed)
    _watch(armed, account, "13385")
    _watch(armed, account, "13379")
    _open(armed, "13385", "13379")
    process_openings(armed, now=SOON)
    items = svc.list_notifications(armed, account, limit=10)
    assert len(items) == 2 and svc.unread_count(armed, account) == 2
    before = {e.id: _event_row(armed, e.id) for _n, e in items}
    later = SOON + timedelta(minutes=5)              # later than the event: any write shows
    svc.mark_read(armed, account, items[0][0].id, now=later)
    svc.mark_read(armed, account, items[0][0].id, now=later + timedelta(minutes=1))  # idempotent
    armed.commit()
    assert svc.unread_count(armed, account) == 1
    assert svc.mark_all_read(armed, account, now=later + timedelta(minutes=2)) == 1
    armed.commit()
    assert svc.unread_count(armed, account) == 0
    assert {e: _event_row(armed, e) for e in before} == before


def test_inbox_pages_newest_first_with_a_stable_cursor(armed) -> None:
    account = _account(armed)
    for index in ("13385", "13379", "13380"):
        _watch(armed, account, index)
    _open(armed, "13385", at=T1)
    process_openings(armed, now=T1)
    _open(armed, "13385", "13379", at=T1 + timedelta(minutes=2))
    process_openings(armed, now=T1 + timedelta(minutes=2))
    _open(armed, "13385", "13379", "13380", at=T1 + timedelta(minutes=4))
    process_openings(armed, now=T1 + timedelta(minutes=4))
    first = svc.list_notifications(armed, account, limit=2)
    rest = svc.list_notifications(armed, account, limit=2,
                                  before=(first[-1][0].created_at, first[-1][0].id))
    order = [e.index_number for _n, e in first + rest]
    assert order == ["13380", "13379", "13385"]


def test_another_account_cannot_read_anothers_items(armed) -> None:
    a, b = _account(armed), _account(armed)
    _watch(armed, a, "13385")
    _open(armed, "13385")
    process_openings(armed, now=SOON)
    [(item, _e)] = svc.list_notifications(armed, a, limit=5)
    with pytest.raises(svc.NotFound):
        svc.mark_read(armed, b, item.id, now=SOON)
    assert svc.list_notifications(armed, b, limit=5) == [] and svc.mark_all_read(
        armed, b, now=SOON) == 0
    armed.refresh(item)
    assert item.read_at is None


# ==========================================================================
# endpoints and Live Activities
# ==========================================================================


def test_tokens_are_reassigned_rotated_and_never_duplicated(armed) -> None:
    a, b = _account(armed), _account(armed)
    first = _device(armed, a, 1)
    again = _device(armed, a, 1)
    assert first.id == again.id                               # idempotent
    moved = _device(armed, b, 1)                              # the phone changed accounts
    armed.refresh(first)
    assert first.revoked_reason == "reassigned" and first.token is None
    assert moved.user_account_id == b and svc.list_endpoints(armed, a) == []
    rotated = svc.register_endpoint(armed, b, kind="apns_device", environment="sandbox",
                                    token=_token(2), now=T1, replaces_endpoint_id=moved.id)
    armed.commit()
    armed.refresh(moved)
    assert (moved.revoked_reason, moved.replaced_by_id) == ("rotated", rotated.id)
    with pytest.raises(svc.NotFound):                         # A cannot touch B's endpoint
        svc.revoke_endpoint(armed, a, rotated.id, now=T1)
    with pytest.raises(svc.Invalid):
        svc.register_endpoint(armed, a, kind="apns_device", environment="sandbox",
                              token="not-hex", now=T1)


def test_a_live_activity_can_only_attach_to_the_callers_watch(armed) -> None:
    a, b = _account(armed), _account(armed)
    watch_a = _watch(armed, a, "13385")
    with pytest.raises(svc.NotFound):
        svc.register_live_activity(armed, b, watch_id=watch_a.id, client_activity_id="x",
                                   push_token=_token(9), environment="sandbox", now=T1)
    armed.rollback()
    assert armed.scalar(select(func.count()).select_from(LiveActivity)) == 0


def test_activity_token_rotation_invalidates_the_old_token(armed) -> None:
    account = _account(armed)
    watch = _watch(armed, account, "13385")
    activity = _activity(armed, account, watch, 1)
    old = activity.endpoint_id
    svc.rotate_live_activity_token(armed, account, activity.id, push_token=_token(5000),
                                   environment="sandbox", now=T1)
    armed.commit()
    old_row = armed.get(DeliveryEndpoint, old)
    assert old_row.revoked_reason == "rotated" and old_row.token is None
    _open(armed, "13385")
    provider = FakeProvider()
    _pipeline(armed, provider)
    from app.domain.live import token_fingerprint
    assert [r.fingerprint for r in provider.sent] == [token_fingerprint(_token(5000))]


def test_activities_expire_after_eight_hours(armed) -> None:
    account = _account(armed)
    activity = _activity(armed, account, _watch(armed, account, "13385"), 1)
    assert svc.expire_live_activities(armed, now=BEFORE + timedelta(hours=7)) == 0
    assert svc.expire_live_activities(armed, now=BEFORE + timedelta(hours=8, seconds=1)) == 1
    armed.commit()
    armed.refresh(activity)
    assert activity.state == "expired"
    assert armed.get(DeliveryEndpoint, activity.endpoint_id).revoked_reason == "expired"


# ==========================================================================
# delivery semantics
# ==========================================================================


def _one_delivery(s, script=None, *, n=1):
    account = _account(s)
    _watch(s, account, "13385")
    endpoint = _device(s, account, n)
    _open(s, "13385")
    process_openings(s, now=SOON)
    svc.fan_out(s, now=SOON)
    return account, endpoint


def test_a_cancelled_watch_suppresses_external_delivery_but_keeps_history(armed) -> None:
    account, _e = _one_delivery(armed)
    watch = armed.scalar(select(NotificationEvent)).watch_id
    deactivate_watch(armed, account, watch, now=SOON)
    armed.commit()
    provider = FakeProvider()
    svc.deliver_due(armed, provider, now=SOON)
    [d] = _deliveries(armed, account)
    assert (d.status, d.suppressed_reason) == ("suppressed", "watch_inactive")
    assert provider.sent == []
    assert armed.scalar(select(func.count()).select_from(NotificationEvent)) == 1
    assert svc.unread_count(armed, account) == 1


def test_cancelling_a_watch_ends_its_live_activity_and_revokes_the_token(armed) -> None:
    account = _account(armed)
    watch = _watch(armed, account, "13385")
    activity = _activity(armed, account, watch, 1)
    deactivate_watch(armed, account, watch.id, now=T1)
    armed.commit()
    armed.refresh(activity)
    assert (activity.state, activity.ended_reason) == ("ended", "watch_deactivated")
    endpoint = armed.get(DeliveryEndpoint, activity.endpoint_id)
    assert endpoint.revoked_reason == "activity_ended" and endpoint.token is None
    assert svc.list_live_activities(armed, account) == []


def test_transient_failures_retry_with_backoff_then_deliver(armed) -> None:
    from app.domain.live import token_fingerprint
    account, _endpoint = _one_delivery(armed)
    fp = token_fingerprint(_token(1))
    provider = FakeProvider({fp: [SendResult(Outcome.RETRY, ErrorClass.PROVIDER_UNAVAILABLE,
                                             503)]})
    svc.deliver_due(armed, provider, now=SOON)
    [d] = _deliveries(armed, account)
    assert (d.status, d.attempts, d.error_class) == ("retry", 1, "provider_unavailable")
    retry_at = d.next_attempt_at.replace(tzinfo=UTC) if d.next_attempt_at.tzinfo is None \
        else d.next_attempt_at
    assert retry_at > SOON
    assert svc.deliver_due(armed, provider, now=SOON).claimed == 0      # not yet due
    svc.deliver_due(armed, provider, now=retry_at + timedelta(seconds=1))
    armed.refresh(d)
    assert (d.status, d.attempts) == ("delivered", 2) and len(provider.sent) == 2


def test_an_invalid_token_revokes_the_endpoint_and_never_retries(armed) -> None:
    from app.domain.live import token_fingerprint
    account, endpoint = _one_delivery(armed)
    provider = FakeProvider({token_fingerprint(_token(1)): [
        SendResult(Outcome.ENDPOINT_INVALID, ErrorClass.ENDPOINT_INVALID, 410, "Unregistered")]})
    svc.deliver_due(armed, provider, now=SOON)
    [d] = _deliveries(armed, account)
    assert (d.status, d.error_class, d.provider_reason) == ("failed_permanent",
                                                            "endpoint_invalid", "Unregistered")
    armed.refresh(endpoint)
    assert endpoint.revoked_reason == "provider_invalid" and endpoint.token is None
    assert svc.deliver_due(armed, provider, now=SOON + timedelta(minutes=30)).claimed == 0


def test_a_permanent_rejection_is_not_retried(armed) -> None:
    from app.domain.live import token_fingerprint
    account, endpoint = _one_delivery(armed)
    provider = FakeProvider({token_fingerprint(_token(1)): [
        SendResult(Outcome.PERMANENT, ErrorClass.REQUEST_INVALID, 400, "BadTopic")]})
    svc.deliver_due(armed, provider, now=SOON)
    [d] = _deliveries(armed, account)
    assert (d.status, d.attempts) == ("failed_permanent", 1)
    armed.refresh(endpoint)
    assert endpoint.revoked_at is None                       # a config bug, not the device
    assert svc.deliver_due(armed, provider, now=SOON + timedelta(minutes=30)).claimed == 0


def test_retries_are_bounded(armed) -> None:
    account, _e = _one_delivery(armed)
    provider = FakeProvider(default=SendResult(Outcome.RETRY, ErrorClass.NETWORK))
    now = SOON
    for _ in range(MAX_ATTEMPTS + 3):
        svc.deliver_due(armed, provider, now=now)
        now += timedelta(seconds=1)
        [d] = _deliveries(armed, account)
        if d.status != "retry":
            break
        when = d.next_attempt_at if d.next_attempt_at.tzinfo else d.next_attempt_at.replace(
            tzinfo=UTC)
        now = max(now, when + timedelta(seconds=1))
    [d] = _deliveries(armed, account)
    assert d.status in ("failed_permanent", "suppressed")     # bounded either way
    assert len(provider.sent) <= MAX_ATTEMPTS
    if d.status == "failed_permanent":
        assert d.error_class == "max_attempts"
    else:
        assert d.suppressed_reason == "event_expired"         # backoff passed the TTL


def test_an_old_event_is_never_pushed(armed) -> None:
    account, _e = _one_delivery(armed)
    provider = FakeProvider()
    svc.deliver_due(armed, provider, now=T1 + EVENT_TTL + timedelta(seconds=1))
    [d] = _deliveries(armed, account)
    assert (d.status, d.suppressed_reason) == ("suppressed", "event_expired")
    assert provider.sent == []


def test_one_failing_endpoint_never_blocks_the_others(armed) -> None:
    from app.domain.live import token_fingerprint
    account = _account(armed)
    _watch(armed, account, "13385")
    for n in (1, 2, 3):
        _device(armed, account, n)
    _open(armed, "13385")
    provider = FakeProvider({token_fingerprint(_token(2)): [
        SendResult(Outcome.ENDPOINT_INVALID, ErrorClass.ENDPOINT_INVALID, 410, "Unregistered")]})
    stats = _pipeline(armed, provider)
    assert (stats.delivered, stats.failed) == (2, 1)


def test_a_disabled_account_is_never_pushed(armed) -> None:
    account, _e = _one_delivery(armed)
    armed.get(UserAccount, account).disabled_at = SOON
    armed.commit()
    provider = FakeProvider()
    svc.deliver_due(armed, provider, now=SOON)
    assert _deliveries(armed, account)[0].suppressed_reason == "account_disabled"
    assert provider.sent == []


def test_a_crashed_claim_is_recovered_after_its_lease(armed) -> None:
    _one_delivery(armed)
    [(delivery_id, _claim)] = svc.claim(armed, now=SOON, limit=10)      # the "crash"
    provider = FakeProvider()
    assert svc.deliver_due(armed, provider, now=SOON + timedelta(seconds=30)).claimed == 0
    svc.deliver_due(armed, provider, now=SOON + timedelta(seconds=91))
    d = armed.get(NotificationDelivery, delivery_id)
    armed.refresh(d)
    assert (d.status, d.attempts) == ("delivered", 2) and len(provider.sent) == 1


def test_a_stale_worker_cannot_overwrite_a_reclaimed_delivery(armed) -> None:
    _one_delivery(armed)
    [(delivery_id, stale)] = svc.claim(armed, now=SOON, limit=10)
    [(_same, fresh)] = svc.claim(armed, now=SOON + timedelta(seconds=91), limit=10)
    assert not svc._record(armed, delivery_id, stale, {"status": "retry", "updated_at": SOON})
    armed.commit()
    d = armed.get(NotificationDelivery, delivery_id)
    armed.refresh(d)
    assert (d.status, d.claim_token) == ("claimed", fresh)


def test_fanout_is_idempotent_and_set_based(armed) -> None:
    from sqlalchemy import event as sa_event
    accounts = [_account(armed) for _ in range(30)]
    for i, a in enumerate(accounts):
        _watch(armed, a, "13385")
        _device(armed, a, i)
    _open(armed, "13385")
    process_openings(armed, now=SOON)
    statements = []
    bind = armed.get_bind()
    listener = lambda *a: statements.append(a[2])
    sa_event.listen(bind, "before_cursor_execute", listener)
    try:
        stats = svc.fan_out(armed, now=SOON)
    finally:
        sa_event.remove(bind, "before_cursor_execute", listener)
    assert stats.deliveries == 30
    assert sum(s.lstrip().upper().startswith("INSERT INTO NOTIFICATION_DELIVERY")
               for s in statements) == 1
    assert svc.fan_out(armed, now=SOON).deliveries == 0
    assert armed.scalar(select(func.count()).select_from(NotificationDelivery)) == 30


def test_the_database_key_alone_prevents_duplicate_deliveries(armed) -> None:
    """SYNTHETIC lost fan-out marker (as after a crash between two commits):
    the opening is fanned out again and UNIQUE(event, endpoint, channel)
    is the only thing left - it must insert nothing."""
    from sqlalchemy import update
    account = _account(armed)
    _watch(armed, account, "13385")
    _device(armed, account, 1)
    _device(armed, account, 2)
    _open(armed, "13385")
    process_openings(armed, now=SOON)
    assert svc.fan_out(armed, now=SOON).deliveries == 2
    armed.execute(update(Notification).values(fanned_out_at=None))
    armed.commit()
    again = svc.fan_out(armed, now=SOON)
    assert (again.notifications, again.deliveries) == (1, 0)
    assert armed.scalar(select(func.count()).select_from(NotificationDelivery)) == 2


def test_schedule_generation_writes_none_of_the_live_tables(armed) -> None:
    from app.services.scheduling.service import generate_schedule

    from .test_live_availability import _student
    account = _account(armed)
    _watch(armed, account, "13385")
    _device(armed, account, 1)
    _open(armed, "13385")
    student = _student(armed)
    tables = [Notification, NotificationDelivery, DeliveryEndpoint, LiveActivity,
              AvailabilityPollSchedule, NotificationEvent, AvailabilityRefresh]
    before = {m.__tablename__: armed.scalar(select(func.count()).select_from(m)) for m in tables}
    generate_schedule(armed, student, term_code=FALL26, courses=["01:750:194"],
                      reference_time=SOON)
    armed.rollback()
    after = {m.__tablename__: armed.scalar(select(func.count()).select_from(m)) for m in tables}
    assert after == before
