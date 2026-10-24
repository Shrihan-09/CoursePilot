"""Term watcher CLI (Phase 7.3).

    python -m coursepilot_ingestion.term_watch_cli run        # one watcher pass
    python -m coursepilot_ingestion.term_watch_cli status     # per-term state

`run` makes at most one courses.json request per candidate term, and only for
terms that are due (daily before classes start, weekly after). It is also a
live-worker step (live_worker.LiveWorker(term_watch=...)). Prints counts and
states only.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib

from app.models import DataSource, RutgersTerm
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from coursepilot_ingestion.cli import DEFAULT_CACHE, ROOT, resolve_database_url
from coursepilot_ingestion.fetchers.soc import SocFetcher
from coursepilot_ingestion.pipelines.availability import AvailabilityRefreshPipeline
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery
from coursepilot_ingestion.term_watch import classify, promotion_evidence, watch


def default_watch(cache: pathlib.Path = DEFAULT_CACHE):
    """The production wiring: real fetcher, pipelines, availability, baseline."""
    fetcher = SocFetcher(cache)

    def fetch(query: SocQuery):
        result = fetcher.fetch_courses(query, use_cache=False)   # watcher must see NOW
        return result.content, result.content_hash

    def run(session: Session, **kw):
        def term_row(q):
            return session.scalar(select(RutgersTerm).where(
                RutgersTerm.term_code == q.term_code, RutgersTerm.campus == q.campus))

        def load_courses(q):
            row = term_row(q)
            return CourseIngestionPipeline(session, cache).run(
                q, coverage=row.coverage_state if row else "partial")

        def load_sections(q):
            return SectionIngestionPipeline(session, cache).run(q)

        def load_availability(s, term):
            q = SocQuery(year=int(term[:4]), term=term[4], campus="NB")
            return AvailabilityRefreshPipeline(s, cache / "availability").run(q)

        def baseline():
            ref = session.scalar(select(DataSource.raw_payload_ref).where(
                DataSource.kind == "rutgers_official_api", DataSource.coverage == "complete",
                DataSource.raw_payload_ref.is_not(None))
                .order_by(DataSource.term_code.desc()).limit(1))
            path = ROOT / pathlib.Path(ref.replace("\\", "/")) if ref else None
            return json.loads(path.read_bytes()) if path and path.exists() else []

        return watch(session, fetch, load_courses=load_courses, load_sections=load_sections,
                     load_availability=load_availability, baseline=baseline, **kw)

    return run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="coursepilot-term-watch")
    p.add_argument("command", choices=("run", "status"))
    p.add_argument("--term", action="append")
    p.add_argument("--database-url")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    engine = create_engine(resolve_database_url(args.database_url), future=True)
    with Session(engine) as session:
        if args.command == "run":
            for o in default_watch(pathlib.Path(args.cache_dir))(session, terms=args.term):
                print(f"{o.term_code}: {o.state:17} {o.action:18} {o.detail}")
        for row in session.scalars(select(RutgersTerm).order_by(RutgersTerm.term_code)):
            print(f"{row.term_code} {classify(row).value:17} ingestion={row.ingestion_state} "
                  f"coverage={row.coverage_state} observations={len(row.observations or [])} "
                  f"courses={row.course_count} sections={row.section_count} "
                  f"promotion_evidence={promotion_evidence(row) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
