"""CoursePilot Live on REAL PostgreSQL (Phase 6.9) - concurrency and races.

Unlike the rest of the ingestion suite (in-memory SQLite, one writer at a
time), these use separate PostgreSQL connections in separate threads against
the dedicated `*_test` database (guard: test_postgres_integration.py).
Structure is the real Fall 2026 component fixture; the first refresh is the
real 2026-10-14 openSections payload; later openings are SYNTHETIC.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.domain.live import ErrorClass, Outcome, SendResult, token_fingerprint
from app.models import DataSource, UserAccount
from app.models.availability import AvailabilityRefresh
from app.models.live import (
    AvailabilityPollSchedule,
    DeliveryEndpoint,
    NotificationDelivery,
)
from app.services import live as svc
from app.services.availability import apply_refresh
from app.services.monitoring import create_watch, deactivate_watch, process_openings
from app.services.push_providers import FakeProvider
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from coursepilot_ingestion.live_worker import LiveWorker
from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import CS_REQUIREMENTS, FIXTURE_DIR
from .test_postgres_integration import DB_URL, requires_postgres

pytestmark = pytest.mark.db

FALL26 = "20269"
T0 = datetime(2026, 10, 14, 2, 29, 51, tzinfo=UTC)
T1 = T0 + timedelta(minutes=2)
SOON = T1 + timedelta(seconds=30)
URL = "https://classes.rutgers.edu/soc/api/openSections.json?year=2026&term=9&campus=NB"
PAYLOAD = json.loads((FIXTURE_DIR / "open_sections_fall2026_20261014T022951Z.json").read_bytes())
_TABLES = ("notification_delivery, live_activity, notification, delivery_endpoint, "
           "availability_poll_schedule, notification_event, availability_change_dispatch, "
           "section_watch, section_availability_change, section_availability, "
           "availability_refresh, course_offering, course, subject, data_source")
RUN = uuid.uuid4().hex                     # tokens unique per run


def _token(n) -> str:
    return hashlib.sha256(f"{RUN}-{n}".encode()).hexdigest()


@pytest.fixture
def engine():
    engine = create_engine(DB_URL, future=True, pool_size=12)
    yield engine
    engine.dispose()


@pytest.fixture
def pg(engine, tmp_path):
    with Session(engine) as s:
        s.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
        s.commit()
        query = SocQuery(year=2026, term="9", campus="NB")
        (tmp_path / "soc_courses_2026_9_NB.json").write_bytes(
            (FIXTURE_DIR / "soc_components_fall2026_sample.json").read_bytes())
        CourseIngestionPipeline(s, tmp_path).run(query, coverage="complete")
        SectionIngestionPipeline(s, tmp_path).run(query)
        RequirementLoader(s).load_file(CS_REQUIREMENTS)
        for source in s.scalars(select(DataSource).where(DataSource.term_code == FALL26)):
            source.retrieved_at = datetime(2026, 9, 8, tzinfo=UTC)
        s.commit()
        assert apply_refresh(s, term_code=FALL26, body=json.dumps(PAYLOAD).encode(),
                             observed_at=T0, source_url=URL).verdict == "accepted"
        s.commit()
    return engine


def _together(n, work):
    barrier, errors, results = threading.Barrier(n), [], [None] * n

    def run(i):
        try:
            barrier.wait()
            results[i] = work(i)
        except Exception as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    return results


def _watchers(engine, n, index="13385", devices=True):
    out = []
    with Session(engine) as s:
        for i in range(n):
            a = UserAccount(identity_provider="dev", external_subject=f"pglive-{uuid.uuid4().hex}")
            s.add(a)
            s.flush()
            create_watch(s, a.id, term_code=FALL26, index_number=index,
                         now=T0 - timedelta(hours=1))
            if devices:
                svc.register_endpoint(s, a.id, kind="apns_device", environment="sandbox",
                                      token=_token(f"{index}-{i}"), now=T0 - timedelta(hours=1))
            out.append(a.id)
        s.commit()
    return out


def _open_and_fan_out(engine, *indexes, at=T1):
    with Session(engine) as s:
        apply_refresh(s, term_code=FALL26, body=json.dumps(PAYLOAD + list(indexes)).encode(),
                      observed_at=at, source_url=URL)
        s.commit()
        process_openings(s, now=at)
        svc.fan_out(s, now=at)


@pytest.fixture(autouse=True)
def _inside_a_registration_window(monkeypatch):
    """The lease tests assume the 90 s in-window cadence (Phase 7.3 made the
    outside-window cadence 30 minutes - see test_course_demand.py)."""
    from app.core import registration_windows as rw

    monkeypatch.setattr(rw, "REGISTRATION_WINDOWS", (rw.RegistrationWindow(
        FALL26, T0 - timedelta(days=1), T0 + timedelta(days=60), "SYNTHETIC test window",
        "synthetic://test"),))


def _count(engine, model, *where):
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


# ==========================================================================
# scheduler
# ==========================================================================


@requires_postgres
def test_two_schedulers_make_one_rutgers_request(pg, tmp_path) -> None:
    with Session(pg) as s:
        s.add(AvailabilityPollSchedule(term_code=FALL26, interval_seconds=90,
                                       backoff_cap_seconds=900))
        s.commit()
    calls, lock = [], threading.Lock()

    def rutgers(request):
        with lock:
            calls.append(1)
        time.sleep(0.3)                                    # the other worker tries meanwhile
        return httpx.Response(200, content=json.dumps(PAYLOAD).encode())

    factory = sessionmaker(pg, expire_on_commit=False)
    workers = [LiveWorker(factory, owner=f"w{i}", archive_dir=tmp_path,
                          transport=httpx.MockTransport(rutgers), clock=lambda: T1)
               for i in range(3)]
    reports = _together(3, lambda i: workers[i].poll_term(FALL26, "NB"))
    assert len(calls) == 1
    assert sorted(r for r in reports if r) == ["accepted"]
    assert _count(pg, AvailabilityRefresh, AvailabilityRefresh.observed_at == T1) == 1


@requires_postgres
def test_a_worker_that_lost_its_lease_writes_nothing(pg, tmp_path) -> None:
    """Worker A's lease expires while its fetch hangs; B takes the term and
    refreshes; A's late result is fenced out."""
    with Session(pg) as s:
        s.add(AvailabilityPollSchedule(term_code=FALL26, interval_seconds=90,
                                       backoff_cap_seconds=900))
        s.commit()
    release, b_done = threading.Event(), threading.Event()
    factory = sessionmaker(pg, expire_on_commit=False)
    now = {"a": T1, "b": T1 + timedelta(seconds=130)}         # B is past A's 120 s lease

    def slow(request):
        release.wait(10)
        return httpx.Response(200, content=json.dumps(PAYLOAD).encode())

    def fast(request):
        return httpx.Response(200, content=json.dumps(PAYLOAD + ["13385"]).encode())

    a = LiveWorker(factory, owner="a", archive_dir=tmp_path, transport=httpx.MockTransport(slow),
                   clock=lambda: now["a"])
    b = LiveWorker(factory, owner="b", archive_dir=tmp_path, transport=httpx.MockTransport(fast),
                   clock=lambda: now["b"])
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("a", a.poll_term(FALL26, "NB")))
    thread.start()
    time.sleep(0.3)                                           # A holds the lease, mid-fetch
    now["a"] = T1 + timedelta(seconds=140)                    # A's clock moves past its lease
    result["b"] = b.poll_term(FALL26, "NB")
    b_done.set()
    release.set()
    thread.join(15)
    assert result["b"] == "accepted" and result["a"] == "lease_lost"
    assert _count(pg, AvailabilityRefresh, AvailabilityRefresh.observed_at > T0) == 1


# ==========================================================================
# delivery
# ==========================================================================


@requires_postgres
@pytest.mark.parametrize("workers", [2, 4])
def test_concurrent_delivery_workers_send_each_delivery_once(pg, workers) -> None:
    _watchers(pg, 40)
    _open_and_fan_out(pg, "13385")
    assert _count(pg, NotificationDelivery) == 40
    provider = FakeProvider()

    def work(_i):
        with Session(pg) as s:
            total = 0
            while True:
                stats = svc.deliver_due(s, provider, now=SOON, limit=5)
                total += stats.delivered
                if not stats.claimed:
                    return total

    sent = _together(workers, work)
    ids = [r.delivery_id for r in provider.sent]
    assert sum(sent) == 40 and len(ids) == 40 and len(set(ids)) == 40
    assert _count(pg, NotificationDelivery, NotificationDelivery.status == "delivered") == 40


@requires_postgres
def test_retry_races_never_duplicate_a_send(pg) -> None:
    [_account] = _watchers(pg, 1)
    _open_and_fan_out(pg, "13385")
    fp = token_fingerprint(_token("13385-0"))
    with Session(pg) as s:
        svc.deliver_due(s, FakeProvider({fp: [SendResult(Outcome.RETRY, ErrorClass.NETWORK)]}),
                        now=SOON)
        when = s.scalar(select(NotificationDelivery.next_attempt_at))
    provider = FakeProvider()
    _together(4, lambda i: svc.deliver_due(Session(pg), provider,
                                           now=when + timedelta(seconds=1)))
    assert len(provider.sent) == 1


@requires_postgres
def test_a_crashed_claim_is_resent_once_after_its_lease(pg) -> None:
    _watchers(pg, 3)
    _open_and_fan_out(pg, "13385")
    with Session(pg) as s:
        claimed = svc.claim(s, now=SOON, limit=10)            # worker "crashes" here
    assert len(claimed) == 3
    provider = FakeProvider()
    with Session(pg) as s:
        assert svc.deliver_due(s, provider, now=SOON + timedelta(seconds=10)).claimed == 0
    _together(3, lambda i: svc.deliver_due(Session(pg), provider,
                                           now=SOON + timedelta(seconds=100)))
    assert len(provider.sent) == 3 and len({r.delivery_id for r in provider.sent}) == 3
    with Session(pg) as s:
        assert set(s.scalars(select(NotificationDelivery.attempts))) == {2}


@requires_postgres
def test_a_watch_cancelled_before_send_suppresses_the_push(pg) -> None:
    [account] = _watchers(pg, 1)
    _open_and_fan_out(pg, "13385")
    with Session(pg) as s:                                    # the user cancels, elsewhere
        watch_id = s.scalar(select(NotificationDelivery.event_id))
        from app.models.monitoring import NotificationEvent
        watch_id = s.get(NotificationEvent, watch_id).watch_id
        deactivate_watch(s, account, watch_id, now=SOON)
        s.commit()
    provider = FakeProvider()
    with Session(pg) as s:
        svc.deliver_due(s, provider, now=SOON)
    assert provider.sent == []
    assert _count(pg, NotificationDelivery, NotificationDelivery.suppressed_reason ==
                  "watch_inactive") == 1


@requires_postgres
def test_token_rotation_and_invalidation_on_postgres(pg) -> None:
    [account] = _watchers(pg, 1, devices=False)
    with Session(pg) as s:
        watch = s.scalar(select(svc.SectionWatch))
        activity = svc.register_live_activity(s, account, watch_id=watch.id,
                                              client_activity_id="pg-1",
                                              push_token=_token("la-old"),
                                              environment="sandbox", now=T0)
        s.commit()
        old_endpoint = activity.endpoint_id
    _open_and_fan_out(pg, "13385")                            # a delivery to the OLD token
    with Session(pg) as s:
        svc.rotate_live_activity_token(s, account, activity.id, push_token=_token("la-new"),
                                       environment="sandbox", now=SOON)
        s.commit()
    provider = FakeProvider()
    with Session(pg) as s:
        svc.deliver_due(s, provider, now=SOON)
        assert s.get(DeliveryEndpoint, old_endpoint).revoked_reason == "rotated"
    assert provider.sent == []                                # never to the stale token
    assert _count(pg, NotificationDelivery,
                  NotificationDelivery.suppressed_reason == "endpoint_revoked") == 1


@requires_postgres
def test_an_invalid_token_is_revoked_once_on_postgres(pg) -> None:
    _watchers(pg, 2)
    _open_and_fan_out(pg, "13385")
    bad = token_fingerprint(_token("13385-0"))
    provider = FakeProvider({bad: [SendResult(Outcome.ENDPOINT_INVALID,
                                              ErrorClass.ENDPOINT_INVALID, 410, "Unregistered")]})
    _together(2, lambda i: svc.deliver_due(Session(pg), provider, now=SOON))
    assert _count(pg, DeliveryEndpoint, DeliveryEndpoint.revoked_reason ==
                  "provider_invalid") == 1
    assert _count(pg, NotificationDelivery, NotificationDelivery.status == "delivered") == 1
