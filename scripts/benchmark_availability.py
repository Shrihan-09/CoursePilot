"""Phase 6.7: availability refresh cost on the full Fall 2026 term - no network.

Replays the real openSections.json payloads archived during the Phase 6.7
investigation (data/raw/live/) into the development database, with their
real observation times:

    1. 2026-10-14T02:29:51Z   first observation  -> every section gets a state
    2. the same payload again                    -> must record 0 transitions
    3. 2026-10-14T02:31:11Z   the real next poll  -> real transitions only
    4. Spring 2027 (`[]`, unpublished)          -> rejected, nothing changes

For each: parse time, apply time, SQL statements, rows written. Then one
batched lookup of EVERY Fall 2026 section (the worst case a schedule could
ask for) and its statement count.

The development database keeps these refreshes afterwards - it is what a
deployment would hold after two polls. Run benchmark_schedules.py with
AVAILABILITY_AT=2026-10-14T02:32:11+00:00 to time the CURRENT path.

Usage:  DATABASE_URL_SYNC=... python scripts/benchmark_availability.py
"""

from __future__ import annotations

import json
import os
import pathlib
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend")]

from app.domain.availability import parse_open_sections
from app.models import CourseSection, SectionAvailabilityChange
from app.services.availability import SectionRef, apply_refresh, lookup
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

LIVE = ROOT / "data" / "raw" / "live"
TERM = "20269"
URL = "https://classes.rutgers.edu/soc/api/openSections.json?year=2026&term=9&campus=NB"
FIRST = LIVE / "openSections_2026_9_NB_20261014T022951Z.json"
SECOND = LIVE / "openSections_2026_9_NB_20261014T023111Z.json"
EMPTY = LIVE / "openSections_2027_1_NB_20261014T022953Z.json"
T1 = datetime(2026, 10, 14, 2, 29, 51, tzinfo=UTC)
T2 = datetime(2026, 10, 14, 2, 31, 11, tzinfo=UTC)


def main() -> None:
    url = os.environ.get("DATABASE_URL_SYNC",
                         "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot")
    engine = create_engine(url)
    statements = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _count(*_args):
        statements["n"] += 1

    a, b = (set(json.loads(p.read_bytes())) for p in (FIRST, SECOND))
    print(json.dumps({"payload_bytes": FIRST.stat().st_size, "entries": len(a),
                      "real_diff_between_polls": {"opened": len(b - a), "closed": len(a - b)},
                      "poll_gap_seconds": (T2 - T1).total_seconds()}))
    parse = []
    for _ in range(50):
        t0 = time.perf_counter()
        parse_open_sections(FIRST.read_bytes())
        parse.append((time.perf_counter() - t0) * 1000)
    print(json.dumps({"parse_p50_ms": round(statistics.median(parse), 2)}))

    with Session(engine) as s:
        for name, path, at in (("first observation", FIRST, T1),
                               ("same payload again", FIRST, T1 + timedelta(seconds=40)),
                               ("real next poll", SECOND, T2),
                               ("empty (Spring 2027)", EMPTY, T2 + timedelta(seconds=5))):
            changes_before = s.scalar(select(func.count())
                                      .select_from(SectionAvailabilityChange))
            statements["n"] = 0
            t0 = time.perf_counter()
            refresh = apply_refresh(s, term_code=TERM, body=path.read_bytes(), observed_at=at,
                                    source_url=URL, archive_path=str(path.relative_to(ROOT)))
            s.commit()
            elapsed = (time.perf_counter() - t0) * 1000
            changes = s.scalar(select(func.count()).select_from(SectionAvailabilityChange))
            print(json.dumps({
                "step": name, "verdict": refresh.verdict, "matched": refresh.matched,
                "ignored": refresh.ignored, "transitions": refresh.transitions,
                "change_rows_written": changes - changes_before,
                "apply_ms": round(elapsed, 1), "sql_statements": statements["n"]}), flush=True)

        refs = [SectionRef(sid, idx, op, src) for sid, idx, op, src in s.execute(
            select(CourseSection.id, CourseSection.index_number, CourseSection.open_status,
                   CourseSection.source_id).where(CourseSection.term_code == TERM))]
        timings = []
        for _ in range(5):
            statements["n"] = 0
            t0 = time.perf_counter()
            resolved, _ = lookup(s, TERM, refs, T2 + timedelta(minutes=1))
            timings.append((time.perf_counter() - t0) * 1000)
        states = {}
        for r in resolved.values():
            key = f"{r.status.value}/{r.freshness.value}"
            states[key] = states.get(key, 0) + 1
        print(json.dumps({"lookup_sections": len(refs),
                          "lookup_p50_ms": round(statistics.median(timings), 1),
                          "lookup_sql_statements": statements["n"],
                          "states": dict(sorted(states.items()))}))
        s.rollback()


if __name__ == "__main__":
    main()
