"""Phase 6.5: Planning Engine latency and query counts on the development data.

Cases (all through app.services.planning.service.generate_plan):

  small   the development student (CS, most of the major done)
  what-if the development student under each other loaded program
  large   a transient student with NOTHING taken, under CS and Math Option A
          (created inside a transaction that is always rolled back)

For each case: p50 / p95 wall time over N runs, SQL statements per plan
(counted with a cursor-execute listener), courses placed, statements per
placed course - flat across small and large means no N+1 - and whether every
run produced a byte-identical plan.

Usage:  DATABASE_URL_SYNC=... python scripts/benchmark_planning.py [runs] [start_term]
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

from app.models import Program, ProgramVersion, School, Student  # noqa: E402
from app.services.planning.service import generate_plan  # noqa: E402
from app.services.programs import program_key  # noqa: E402

RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 7
START = sys.argv[2] if len(sys.argv) > 2 else "20271"


def _pct(values, p):
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

    out = []
    with Session(engine) as s:
        dev = s.scalar(select(Student).where(Student.external_ref == "smoke-1"))
        keys = sorted(program_key(sc.code, p.code, p.degree_type, p.variant)
                      for p, sc in s.execute(select(Program, School)
                                             .join(School, School.id == Program.school_id)))
        cases = [("small", dev, None)]
        cases += [("what-if", dev, k) for k in keys if k != "sas-198-ba"]

        # Transient students: flushed, never committed (rolled back below).
        for code, key in (("198", "sas-198-ba"), ("640", "sas-640-ba-option-a")):
            version = s.scalar(select(ProgramVersion).join(Program)
                               .where(Program.code == code))
            fresh = Student(external_ref=f"bench-fresh-{code}", catalog_year=version.catalog_year,
                            program_version_id=version.id)
            s.add(fresh)
            s.flush()
            cases.append(("large", fresh, key))

        for kind, student, key in cases:
            times, counts, outputs = [], [], set()
            plan = None
            for _ in range(RUNS):
                statements["n"] = 0
                t = time.perf_counter()
                plan = generate_plan(s, student, start_term=START, program_key_=key)
                times.append((time.perf_counter() - t) * 1000)
                counts.append(statements["n"])
                outputs.add(plan.canonical_json())
            placed = sum(len(t.courses) for t in plan.terms)
            out.append({
                "case": kind, "student": student.external_ref,
                "program": plan.target.program_key, "status": plan.status.value,
                "terms": len(plan.terms), "placed": placed,
                "p50_ms": round(statistics.median(times), 1),
                "p95_ms": round(_pct(times, 95), 1),
                "statements": counts[-1],
                # The first run also lazy-loads ORM relationships; warm runs
                # must issue exactly the same statements.
                "statements_cold": counts[0],
                "statements_stable_warm": len(set(counts[1:])) <= 1,
                "identical_plans": len(outputs) == 1,
                "statements_per_placed": round(counts[-1] / max(placed, 1), 1),
            })
            print(json.dumps(out[-1]), flush=True)
        s.rollback()           # the transient students never existed
    print(json.dumps({"runs": RUNS, "start_term": START, "cases": out}))


if __name__ == "__main__":
    main()
