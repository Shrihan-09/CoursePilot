"""Section monitoring on REAL PostgreSQL - concurrency and the core flow (Phase 6.8).

The rest of the ingestion suite runs on in-memory SQLite (conftest's
`session`), which serializes every writer and so cannot show a race. These
tests use separate PostgreSQL connections in separate threads, started
together, against the dedicated `*_test` database (see
test_postgres_integration.py for the guard and setup).

Structure is the real Fall 2026 component fixture; the first refresh is the
real 2026-10-14 openSections payload; later polls are SYNTHETIC copies.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.models import DataSource, UserAccount
from app.models.availability import (
    SectionAvailability,
    SectionAvailabilityChange,
)
from app.models.monitoring import (
    AvailabilityChangeDispatch,
    NotificationEvent,
    SectionWatch,
)
from app.services.availability import apply_refresh
from app.services.monitoring import create_watch, process_openings
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

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
URL = "https://classes.rutgers.edu/soc/api/openSections.json?year=2026&term=9&campus=NB"
PAYLOAD = json.loads((FIXTURE_DIR / "open_sections_fall2026_20261014T022951Z.json").read_bytes())
_TABLES = ("notification_event, availability_change_dispatch, section_watch, "
           "section_availability_change, section_availability, availability_refresh, "
           "course_offering, course, subject, data_source")


@pytest.fixture
def engine():
    engine = create_engine(DB_URL, future=True, pool_size=8)
    yield engine
    engine.dispose()


@pytest.fixture
def pg(engine, tmp_path):
    """Real Fall 2026 component sections + the real first observation, on PostgreSQL."""
    with Session(engine) as s:
        s.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
        s.commit()
        query = SocQuery(year=2026, term="9", campus="NB")
        (tmp_path / "soc_courses_2026_9_NB.json").write_bytes(
            (FIXTURE_DIR / "soc_components_fall2026_sample.json").read_bytes())
        CourseIngestionPipeline(s, tmp_path).run(query, coverage="complete")
        SectionIngestionPipeline(s, tmp_path).run(query)
        RequirementLoader(s).load_file(CS_REQUIREMENTS)
        # The copy's file time is "now"; the payload was downloaded 2026-09-08.
        for source in s.scalars(select(DataSource).where(DataSource.term_code == FALL26)):
            source.retrieved_at = datetime(2026, 9, 8, tzinfo=UTC)
        s.commit()
        first = apply_refresh(s, term_code=FALL26, body=json.dumps(PAYLOAD).encode(),
                              observed_at=T0, source_url=URL)
        s.commit()
        assert first.verdict == "accepted"
    return engine


def _accounts(engine, n):
    with Session(engine) as s:
        ids = []
        for _ in range(n):
            a = UserAccount(identity_provider="dev", external_subject=f"pgmon-{uuid.uuid4().hex}")
            s.add(a)
            s.flush()
            ids.append(a.id)
        s.commit()
        return ids


def _watch_all(engine, accounts, index):
    with Session(engine) as s:
        for a in accounts:
            create_watch(s, a, term_code=FALL26, index_number=index, now=T0 - timedelta(hours=1))
        s.commit()


def _open(engine, *indexes, at=T1):
    with Session(engine) as s:
        refresh = apply_refresh(s, term_code=FALL26, body=json.dumps(PAYLOAD + list(indexes))
                                .encode(), observed_at=at, source_url=URL)
        s.commit()
        return refresh.transitions


def _count(engine, model, *where):
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def _together(n, work):
    """Run work(i) in n threads released at the same instant; re-raise failures."""
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


@requires_postgres
def test_core_flow_on_postgres(pg) -> None:
    """A/B on PostgreSQL: one event per watcher for a SYNTHETIC opening of the
    real lab 01:750:194 LB 13385; none for an OPEN -> OPEN watcher."""
    watchers = _accounts(pg, 3)
    _watch_all(pg, watchers, "13385")
    _watch_all(pg, _accounts(pg, 1), "13384")                # real: open at T0, stays open
    assert _open(pg, "13385") == 1
    with Session(pg) as s:
        stats = process_openings(s, now=T1 + timedelta(seconds=30))
        events = s.scalars(select(NotificationEvent).order_by(NotificationEvent.user_account_id))
        rows = [(e.user_account_id, e.index_number, e.required_component_state,
                 e.required_components_open, e.required_components_total) for e in events]
    assert (stats.openings, stats.events_created) == (1, 3)
    assert sorted(r[0] for r in rows) == sorted(watchers)
    assert {r[1:] for r in rows} == {("13385", "closed", 0, 8)}  # every 194 lecture closed


@requires_postgres
@pytest.mark.parametrize("workers", [2, 4])
def test_concurrent_workers_create_exactly_one_event_per_watch(pg, workers) -> None:
    watchers = _accounts(pg, 25)
    _watch_all(pg, watchers, "13385")
    _watch_all(pg, watchers[:10], "13379")
    _open(pg, "13385", "13379")

    def work(_i):
        with Session(pg) as s:
            return process_openings(s, now=T1 + timedelta(seconds=30), batch=1)

    stats = _together(workers, work)
    assert sum(st.events_created for st in stats) == 35            # exactly one per watch
    assert _count(pg, NotificationEvent) == 35
    assert _count(pg, AvailabilityChangeDispatch) == 2
    with Session(pg) as s:
        pairs = s.execute(select(NotificationEvent.watch_id, NotificationEvent.change_id)).all()
    assert len(pairs) == len(set(pairs))


@requires_postgres
def test_concurrent_identical_watch_requests_make_one_watch(pg) -> None:
    [account] = _accounts(pg, 1)

    def work(_i):
        with Session(pg) as s:
            watch, created = create_watch(s, account, term_code=FALL26, index_number="13385",
                                          now=T0)
            s.commit()
            return watch.id, created

    results = _together(6, work)
    assert len({w for w, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    assert _count(pg, SectionWatch, SectionWatch.user_account_id == account) == 1


@requires_postgres
def test_concurrent_refreshes_record_a_transition_once(pg) -> None:
    """Forced interleaving: refresh 1 has applied 'closed -> open' but not yet
    committed when refresh 2 of the same payload starts. Without the per-term
    lock, refresh 2 reads the OLD snapshot and records the same opening again
    - two change rows, two events per watcher. With it, refresh 2 waits,
    then sees the new state and records nothing."""
    watchers = _accounts(pg, 3)
    _watch_all(pg, watchers, "13385")
    body = json.dumps(PAYLOAD + ["13385"]).encode()
    first = Session(pg)
    try:
        held = apply_refresh(first, term_code=FALL26, body=body, observed_at=T1,
                             source_url=URL)
        assert held.transitions == 1                         # applied, NOT committed
        second: dict = {}

        def later():
            with Session(pg) as s:
                refresh = apply_refresh(s, term_code=FALL26, body=body,
                                        observed_at=T1 + timedelta(seconds=5), source_url=URL)
                s.commit()
                second["transitions"] = refresh.transitions

        thread = threading.Thread(target=later)
        thread.start()
        thread.join(timeout=2.0)                             # it must be waiting on refresh 1
        assert thread.is_alive(), "refresh 2 did not wait for refresh 1"
        first.commit()
        thread.join(timeout=60)
        assert not thread.is_alive()
    finally:
        first.close()
    assert second["transitions"] == 0
    assert _count(pg, SectionAvailabilityChange,
                  SectionAvailabilityChange.index_number == "13385",
                  SectionAvailabilityChange.to_status == "open") == 1
    with Session(pg) as s:
        process_openings(s, now=T1 + timedelta(minutes=1))
    assert _count(pg, NotificationEvent) == 3
    assert _count(pg, SectionAvailability, SectionAvailability.index_number == "13385",
                  SectionAvailability.status == "open") == 1


@requires_postgres
def test_a_crash_before_commit_loses_nothing(pg) -> None:
    """Changes committed, processor dies mid-batch (rolled back): the opening
    is still pending and the next run creates every event once."""
    watchers = _accounts(pg, 5)
    _watch_all(pg, watchers, "13385")
    _open(pg, "13385")
    with Session(pg) as s:
        process_openings(s, now=T1, commit=False)
        assert s.scalar(select(func.count()).select_from(NotificationEvent)) == 5
        s.rollback()                                             # the "crash"
    assert _count(pg, NotificationEvent) == 0
    with Session(pg) as s:
        assert process_openings(s, now=T1).events_created == 5
    assert _count(pg, NotificationEvent) == 5
