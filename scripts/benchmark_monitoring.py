"""Phase 6.8: section monitoring cost on the full Fall 2026 term - no network, no residue.

For 0 / 1 / 10 / 100 / 1,000 watches spread over the sections that open:

  * Rutgers requests for one refresh (a counting transport - never the network)
  * watch creation (per call), listing (one account), event processing,
    event listing; SQL statements for processing
  * events created

Each case runs inside ONE transaction on the development database and is
rolled back: no account, watch, refresh or event survives the benchmark.
The openings are SYNTHETIC: the latest real payload plus 5 closed sections.

Usage:  DATABASE_URL_SYNC=... python scripts/benchmark_monitoring.py
"""

from __future__ import annotations

import json
import os
import pathlib
import statistics
import sys
import time
import uuid
from datetime import timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "ingestion")]

import httpx
from app.models import UserAccount
from app.models.availability import (
    AvailabilityRefresh,
    SectionAvailability,
)
from app.services.monitoring import (
    create_watch,
    list_events,
    list_watches,
    process_openings,
)
from coursepilot_ingestion.pipelines.availability import (
    AvailabilityFetch,
    AvailabilityRefreshPipeline,
)
from coursepilot_ingestion.sources.soc import SocQuery
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

TERM = "20269"
LIVE = ROOT / "data" / "raw" / "live"
LATEST = LIVE / "openSections_2026_9_NB_20261014T023111Z.json"
OPENING = 5


def main() -> None:
    url = os.environ.get("DATABASE_URL_SYNC",
                         "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot")
    engine = create_engine(url)
    statements = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _count(*_args):
        statements["n"] += 1

    payload = json.loads(LATEST.read_bytes())
    for watches in (0, 1, 10, 100, 1000):
        with Session(engine) as s:
            latest = s.scalar(select(AvailabilityRefresh.observed_at)
                              .where(AvailabilityRefresh.term_code == TERM,
                                     AvailabilityRefresh.verdict == "accepted")
                              .order_by(AvailabilityRefresh.observed_at.desc()).limit(1))
            closed = s.scalars(select(SectionAvailability.index_number)
                               .where(SectionAvailability.term_code == TERM,
                                      SectionAvailability.status == "closed")
                               .order_by(SectionAvailability.index_number).limit(OPENING)).all()
            created_at = latest - timedelta(minutes=5)
            accounts = []
            for _ in range(max(1, -(-watches // OPENING))):     # 5 distinct watches each
                a = UserAccount(identity_provider="bench", external_subject=uuid.uuid4().hex)
                s.add(a)
                s.flush()
                accounts.append(a.id)
            create_ms = []
            for i in range(watches):
                t0 = time.perf_counter()
                create_watch(s, accounts[i // OPENING], term_code=TERM,
                             index_number=closed[i % OPENING], now=created_at)
                create_ms.append((time.perf_counter() - t0) * 1000)
            # One refresh, through the real pipeline, with a counting transport.
            requests = []
            body = json.dumps(payload + list(closed)).encode()
            pipeline = AvailabilityRefreshPipeline(
                s, ROOT / "data" / "raw" / "bench_tmp",
                transport=httpx.MockTransport(
                    lambda r, seen=requests, body=body: (seen.append(str(r.url)),
                                                         httpx.Response(200, content=body))[1]))
            at = latest + timedelta(minutes=2)
            fetched = pipeline.fetch(SocQuery(year=2026, term="9", campus="NB"))
            fetched = AvailabilityFetch(fetched.url, at, fetched.body)
            refresh = pipeline.run(SocQuery(year=2026, term="9", campus="NB"),
                                   fetched=fetched, commit=False, process_events=False)
            statements["n"] = 0
            t0 = time.perf_counter()
            stats = process_openings(s, now=at + timedelta(seconds=30), commit=False)
            process_ms = (time.perf_counter() - t0) * 1000
            process_sql = statements["n"]
            t0 = time.perf_counter()
            mine = list_watches(s, accounts[0])
            list_ms = (time.perf_counter() - t0) * 1000
            t0 = time.perf_counter()
            evs = list_events(s, accounts[0], limit=50)
            events_ms = (time.perf_counter() - t0) * 1000
            sizes = {t: s.execute(text(f"SELECT avg(pg_column_size(x.*)) FROM {t} x")).scalar()
                     for t in ("section_watch", "notification_event")} if watches else {}
            print(json.dumps({
                "watches": watches, "accounts": len(accounts),
                "rutgers_requests": len(requests), "transitions": refresh.transitions,
                "openings": stats.openings, "events_created": stats.events_created,
                "create_watch_p50_ms": round(statistics.median(create_ms), 2) if create_ms
                else None,
                "process_ms": round(process_ms, 1), "process_sql_statements": process_sql,
                "list_watches_ms": round(list_ms, 2), "watches_listed": len(mine),
                "list_events_ms": round(events_ms, 2), "events_listed": len(evs),
                "avg_row_bytes": {k: round(float(v)) for k, v in sizes.items() if v}}),
                  flush=True)
            s.rollback()
    for leftover in (ROOT / "data" / "raw" / "bench_tmp").glob("*.json"):
        leftover.unlink()


if __name__ == "__main__":
    main()
