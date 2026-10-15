"""Phase 6.9: CoursePilot Live cost on the FULL Fall 2026 term - no network, no Apple.

Runs against the disposable `*_test` database (the PostgreSQL test suites
TRUNCATE it anyway). Loads the real archived Fall 2026 download (11,992
sections) through the production pipelines, applies the real 2026-10-14
openSections payload, then for 1 / 10 / 100 / 1,000 watchers of ONE section
(each with an APNs device endpoint; SYNTHETIC opening):

  * scheduler tick: Rutgers requests (a counting MockTransport), refresh time
  * refresh -> events (process_openings), events -> deliveries (fan_out)
  * delivery worker throughput (FakeProvider - no request leaves the machine)
  * SQL statements per stage; inbox listing and unread count

Usage:  TEST_DATABASE_URL=...coursepilot_test python scripts/benchmark_live.py
"""

# ruff: noqa: B023 - each lambda is used within its own loop iteration only
from __future__ import annotations

import json
import os
import pathlib
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "ingestion")]

import httpx
from app.models import UserAccount
from app.models.availability import SectionAvailability
from app.models.live import AvailabilityPollSchedule
from app.services import live as svc
from app.services.monitoring import create_watch, process_openings
from app.services.push_providers import FakeProvider
from coursepilot_ingestion.live_worker import LiveWorker
from coursepilot_ingestion.pipelines.courses import (
    CourseIngestionPipeline,
)
from coursepilot_ingestion.pipelines.sections import (
    SectionIngestionPipeline,
)
from coursepilot_ingestion.sources.soc import SocQuery
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

URL = os.environ.get("TEST_DATABASE_URL",
                     "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test")
assert URL.rstrip("/").endswith("_test"), "refusing to run outside a *_test database"
LIVE = ROOT / "data" / "raw" / "live"
COURSES = LIVE / "courses_2026_9_NB_20261014T023008Z.json"
OPEN = json.loads((LIVE / "openSections_2026_9_NB_20261014T022951Z.json").read_bytes())
TERM = "20269"
T0 = datetime(2026, 10, 14, 2, 29, 51, tzinfo=UTC)
TABLES = ("notification_delivery, live_activity, notification, delivery_endpoint, "
          "availability_poll_schedule, notification_event, availability_change_dispatch, "
          "section_watch, section_availability_change, section_availability, "
          "availability_refresh, course_offering, course, subject, data_source")


def main() -> None:
    engine = create_engine(URL, pool_size=10)
    count = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _c(*_a):
        count["n"] += 1

    factory = sessionmaker(engine, expire_on_commit=False)
    folder = ROOT / "data" / "raw" / "bench_live"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "soc_courses_2026_9_NB.json").write_bytes(COURSES.read_bytes())
    for watchers in (1, 10, 100, 1000):
        with Session(engine) as s:
            s.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
            s.commit()
            t = time.perf_counter()
            query = SocQuery(year=2026, term="9", campus="NB")
            CourseIngestionPipeline(s, folder).run(query, coverage="complete")
            SectionIngestionPipeline(s, folder).run(query)
            load_s = time.perf_counter() - t
            s.add(AvailabilityPollSchedule(term_code=TERM, interval_seconds=90,
                                           backoff_cap_seconds=900))
            s.commit()
        requests = []

        def rutgers(request, payload):
            requests.append(1)
            return httpx.Response(200, content=json.dumps(payload).encode())

        clock = {"now": T0}
        first = LiveWorker(factory, owner="bench", archive_dir=folder,
                           transport=httpx.MockTransport(lambda r: rutgers(r, OPEN)),
                           clock=lambda: clock["now"])
        t = time.perf_counter()
        first.poll_term(TERM, "NB")                         # the first observation (11,992 rows)
        first_ms = (time.perf_counter() - t) * 1000
        with Session(engine) as s:
            closed = s.scalar(select(SectionAvailability.index_number).where(
                SectionAvailability.term_code == TERM, SectionAvailability.status == "closed")
                .order_by(SectionAvailability.index_number).limit(1))
            accounts = []
            t = time.perf_counter()
            for i in range(watchers):
                a = UserAccount(identity_provider="bench", external_subject=uuid.uuid4().hex)
                s.add(a)
                s.flush()
                create_watch(s, a.id, term_code=TERM, index_number=closed,
                             now=T0 - timedelta(hours=1))
                svc.register_endpoint(s, a.id, kind="apns_device", environment="sandbox",
                                      token=uuid.uuid4().hex * 2, now=T0 - timedelta(hours=1))
                accounts.append(a.id)
            s.commit()
            setup_ms = (time.perf_counter() - t) * 1000
        clock["now"] = T0 + timedelta(seconds=91)
        second = LiveWorker(factory, owner="bench", archive_dir=folder,
                            transport=httpx.MockTransport(lambda r: rutgers(r, OPEN + [closed])),
                            clock=lambda: clock["now"])
        stages = {}
        with Session(engine) as s:
            for name, work in (
                    ("refresh_ms", lambda: second.poll_term(TERM, "NB")),
                    ("events_ms", lambda: process_openings(s, now=clock["now"])),
                    ("fanout_ms", lambda: svc.fan_out(s, now=clock["now"])),
                    ("deliver_ms", lambda: _drain(s, clock["now"]))):
                count["n"] = 0
                t = time.perf_counter()
                result = work()
                stages[name] = round((time.perf_counter() - t) * 1000, 1)
                stages[name.replace("_ms", "_sql")] = count["n"]
                if name == "deliver_ms":
                    stages["delivered"] = result
            inbox, unread = [], []
            for _ in range(20):
                t = time.perf_counter()
                svc.list_notifications(s, accounts[0], limit=20)
                inbox.append((time.perf_counter() - t) * 1000)
                t = time.perf_counter()
                svc.unread_count(s, accounts[0])
                unread.append((time.perf_counter() - t) * 1000)
            s.rollback()
        count["n"] = 0
        t = time.perf_counter()
        idle = LiveWorker(factory, owner="bench", archive_dir=folder,
                          transport=httpx.MockTransport(lambda r: rutgers(r, OPEN)),
                          clock=lambda: clock["now"])
        idle.poll_term(TERM, "NB")                          # not due: claim, recheck, release
        idle_ms = round((time.perf_counter() - t) * 1000, 1)
        print(json.dumps({
            "watchers": watchers, "rutgers_requests": len(requests),
            "term_load_s": round(load_s, 1), "first_refresh_ms": round(first_ms),
            "watch_setup_ms_per_watch": round(setup_ms / watchers, 2),
            **stages, "deliveries_per_s": round(stages["delivered"] / (stages["deliver_ms"] /
                                                                       1000), 1)
            if stages["deliver_ms"] else None,
            "idle_tick_ms": idle_ms, "idle_tick_sql": count["n"],
            "inbox_p50_ms": round(statistics.median(inbox), 2),
            "unread_p50_ms": round(statistics.median(unread), 2)}), flush=True)
    engine.dispose()


def _drain(s, now) -> int:
    provider = FakeProvider()
    total = 0
    while True:
        stats = svc.deliver_due(s, provider, now=now, limit=100)
        total += stats.delivered
        if not stats.claimed:
            return total


if __name__ == "__main__":
    main()
