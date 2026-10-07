"""Phase 6.6: Schedule Engine latency, query counts and search-space size.

Development data (Fall 2026, 20269), the development student. For each case:

  * full request (generate_schedule, including the course-eligibility check)
  * stages: candidate loading (SQL), search + ranking (pure)
  * SQL statements per request (cursor-execute listener)
  * search nodes / pruned branches vs the Cartesian product of sections

The high-section case uses the five largest Fall 2026 lecture courses. Some
have prerequisites the development student lacks, so for that case the
stages are timed directly (load_term + engine.schedule) - the search is what
is being measured there; the eligibility check is measured in the others.

Usage:  DATABASE_URL_SYNC=... python scripts/benchmark_schedules.py [runs]
"""

from __future__ import annotations

import json
import os
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend")]

from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.domain.schedule import SchedulePreferences  # noqa: E402
from app.models import Student  # noqa: E402
from app.services.scheduling.candidates import load_term, student_attributes  # noqa: E402
from app.services.scheduling.engine import schedule  # noqa: E402
from app.services.scheduling.service import generate_schedule  # noqa: E402

RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 15
TERM = "20269"
CASES = {
    "3 courses": ["01:198:352", "01:198:334", "01:640:300"],
    "4 courses (with lab companion)": ["01:198:352", "01:198:334", "01:640:300", "01:750:193"],
    "5 courses": ["01:830:101", "01:920:101", "01:070:101", "01:450:102", "01:198:107"],
    "5 courses, 1-hour break": ["01:830:101", "01:920:101", "01:070:101", "01:450:102",
                                "01:198:107"],
}
HIGH = ["01:355:101", "01:119:115", "01:090:120", "01:198:111", "01:160:161"]
#: Phase 6.6.1: two LB lab bundles, an academic co-requisite pair (205 + 203)
#: and a lab course with a partial co-requisite (119:117 + 119:116).
COMPONENTS = ["01:750:193", "01:750:203", "01:750:205", "01:119:116", "01:119:117"]


def pct(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


def main() -> None:
    url = os.environ.get("DATABASE_URL_SYNC",
                         "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot")
    engine = create_engine(url)
    statements = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _count(*_args):
        statements["n"] += 1

    rows = []
    with Session(engine) as s:
        student = s.scalar(select(Student).where(Student.external_ref == "smoke-1"))
        attrs = student_attributes(s, student)
        for name, courses in [*CASES.items(), ("high-section (engine stages)", HIGH),
                              ("component-heavy (engine stages)", COMPONENTS)]:
            prefs = (SchedulePreferences(minimum_minutes_between_classes=60)
                     if "break" in name else SchedulePreferences())
            full, load, search_rank, counts, outputs = [], [], [], [], set()
            result = None
            for _ in range(RUNS):
                statements["n"] = 0
                t0 = time.perf_counter()
                term = load_term(s, TERM, courses, attrs)
                t1 = time.perf_counter()
                result = schedule(term, sorted(set(courses)), prefs, 10, [])
                t2 = time.perf_counter()
                load.append((t1 - t0) * 1000)
                search_rank.append((t2 - t1) * 1000)
                if "engine stages" not in name:
                    statements["n"] = 0
                    t3 = time.perf_counter()
                    result = generate_schedule(s, student, term_code=TERM, courses=courses,
                                               preferences=prefs)
                    full.append((time.perf_counter() - t3) * 1000)
                counts.append(statements["n"])
                outputs.add(result.canonical_json())
            st = result.metadata.search
            rows.append({
                "case": name, "status": result.status.value, "options": len(result.options),
                "full_p50_ms": round(statistics.median(full), 1) if full else None,
                "full_p95_ms": round(pct(full, 95), 1) if full else None,
                "load_p50_ms": round(statistics.median(load), 1),
                "search_rank_p50_ms": round(statistics.median(search_rank), 1),
                "search_rank_p95_ms": round(pct(search_rank, 95), 1),
                "sql_statements": counts[-1],
                "sections_per_course": st.candidates_per_course,
                "cartesian_product": st.cartesian_product,
                "pattern_product": st.pattern_product,
                "nodes": st.nodes, "pruned": st.pruned, "bound_pruned": st.bound_pruned,
                "schedules_reached": st.solutions_considered,
                "limit_reached": st.limit_reached,
                "identical_runs": len(outputs) == 1,
            })
            print(json.dumps(rows[-1]), flush=True)
        s.rollback()


if __name__ == "__main__":
    main()
