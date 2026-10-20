"""Phase 7.2.2: modeled-progress and WebReg import latency on a THROWAWAY database.

Creates `coursepilot_bench722_<hex>_disposable`, migrates it to head, loads the
public SOC/program fixtures the ingestion suite uses (CS sample for four terms,
the curated CS major, SAS Core), then measures - for fabricated students with
nothing / part / all of the CS major done (+ in-progress work):

  progress   build_progress(): p50 / p95 wall time, SQL statements per call,
             byte-identical output across runs (generated_at excluded)
  webreg     positioned extraction in-process, the isolated killable child
             process, parse_schedule, and SOC matching (resolve_rows) for a
             fabricated 10-row schedule

The database is dropped whatever happens. Development and test databases are
never touched. No real student data is used.

Usage (from the repository root):
    python scripts/benchmark_progress.py [runs] \\
        [--server postgresql+psycopg://coursepilot:coursepilot@localhost:5432/postgres]
"""

from __future__ import annotations

import os
import pathlib
import statistics
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "ingestion"), str(ROOT / "backend")]  # ingestion tests first

from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
RUNS = int(ARGS[0]) if ARGS else 15
SERVER = next(
    (a.split("=", 1)[1] for a in sys.argv if a.startswith("--server=")),
    "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/postgres",
)


def _pct(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


def _ms(values):
    return f"p50 {_pct(values, 50) * 1000:7.1f} ms  p95 {_pct(values, 95) * 1000:7.1f} ms"


def _migrate(url: str) -> None:
    env = {**os.environ, "DATABASE_URL": url, "DATABASE_URL_SYNC": url}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT / "backend",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode:
        raise SystemExit(f"migration failed:\n{proc.stdout}{proc.stderr}")


def bench(url: str) -> None:
    from sqlalchemy.orm import Session

    from app.services.progress import build_progress
    from app.services.scheduling.imports import resolve_rows
    from app.services.scheduling.pdf_worker import _layout_child, extract_layout
    from app.domain.webreg import parse_schedule
    from app.services.transcripts.extract import isolated_pdf_extractor
    from backend_tests_support import build_schedule_pdf, objects  # type: ignore[import-not-found]
    from coursepilot_ingestion.loaders.requirements import RequirementLoader
    from coursepilot_ingestion.pipelines.core import CoreIngestionPipeline
    from tests.conftest import CS_REQUIREMENTS, CS_SAMPLE, SAS_CORE
    from tests.test_planning_engine import CS_DONE, _records, _student, _terms

    engine = create_engine(url)
    statements = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _count(*_args):
        statements["n"] += 1

    with Session(engine) as s, tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        _terms(
            s,
            tmp_path,
            _records("soc_cs_courses_sample.json"),
            spring_omit={"01:198:352", "01:198:416"},
            spring27={"01:198:314", "01:198:336"},
        )
        RequirementLoader(s).load_file(CS_REQUIREMENTS)
        s.commit()
        archive = tmp_path / "soc_courses_2026_9_NB.json"
        archive.write_bytes(CS_SAMPLE.read_bytes())
        CoreIngestionPipeline(s).run(SAS_CORE, archive, observed_term_code="20269")
        s.commit()

        cases = {
            "nothing taken": [],
            "part of CS": CS_DONE[:4],
            "all CS + in-progress": CS_DONE + [("01:198:352", "20271", "in_progress")],
        }
        print(f"progress (build_progress, {RUNS} runs each)")
        for label, records in cases.items():
            student = _student(s, records)
            times, counts, outputs = [], [], set()
            for _ in range(RUNS):
                s.expire_all()
                statements["n"] = 0
                start = time.perf_counter()
                result = build_progress(s, student)
                times.append(time.perf_counter() - start)
                counts.append(statements["n"])
                outputs.add(result.model_dump_json(exclude={"generated_at"}))
            print(
                f"  {label:22} {_ms(times)}  SQL/call {statistics.median(counts):4.0f}  "
                f"identical={len(outputs) == 1}"
            )

        records = [
            (70 + 100 * (i % 5), 640 - 25 * (i // 5), f"01:999:{100 + i}:01-{91200 + i} (3.0)")
            for i in range(10)
        ]
        pdf = build_schedule_pdf(objects(records=records))
        times, isolated, parse, match = [], [], [], []
        extractor = isolated_pdf_extractor(
            max_pages=20, max_chars=200000, timeout_seconds=15, _target=_layout_child
        )
        rows = []
        for _ in range(RUNS):
            start = time.perf_counter()
            result = extract_layout(pdf, 20, 200000)
            times.append(time.perf_counter() - start)
            start = time.perf_counter()
            rows = parse_schedule(result[1], result[3])
            parse.append(time.perf_counter() - start)
            start = time.perf_counter()
            resolve_rows(s, uuid.uuid4(), rows)
            match.append(time.perf_counter() - start)
        for _ in range(min(RUNS, 5)):
            start = time.perf_counter()
            extractor(pdf)
            isolated.append(time.perf_counter() - start)
        s.rollback()
        print(f"webreg (fabricated {len(rows)}-row schedule)")
        print(f"  extract in-process     {_ms(times)}")
        print(f"  extract isolated child {_ms(isolated)}  (spawned process, {len(isolated)} runs)")
        print(f"  parse_schedule         {_ms(parse)}")
        print(f"  resolve_rows (SOC)     {_ms(match)}")
    engine.dispose()


def main() -> None:
    # The fabricated WebReg builder lives with the backend tests.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "backend_tests_support", ROOT / "backend" / "tests" / "support" / "synthetic_webreg.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules["backend_tests_support"] = module

    name = f"coursepilot_bench722_{uuid.uuid4().hex[:8]}_disposable"
    admin = create_engine(SERVER, isolation_level="AUTOCOMMIT")
    url = make_url(SERVER).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        _migrate(url)
        os.environ["DATABASE_URL"] = os.environ["DATABASE_URL_SYNC"] = url
        bench(url)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
        print(f"dropped {name}")


if __name__ == "__main__":
    main()
