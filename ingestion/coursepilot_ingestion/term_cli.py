"""Term discovery and ingestion CLI (Phase 7.2.4).

    python -m coursepilot_ingestion.term_cli discover        # probe the rolling window
    python -m coursepilot_ingestion.term_cli ingest          # load + validate published terms
    python -m coursepilot_ingestion.term_cli status          # what is published / ingested / ready

Low frequency by design (e.g. daily): term STRUCTURE changes slowly, while
open/closed status keeps its own lightweight live poll (live_worker). Prints
counts and states only.
"""

from __future__ import annotations

import argparse
import logging

import httpx
from app.models import RutgersTerm
from app.models.terms import INGESTED, PUBLISHED
from app.services.term_authority import registry_state
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from coursepilot_ingestion.cli import DEFAULT_CACHE, resolve_database_url
from coursepilot_ingestion.fetchers.soc import SocFetcher
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SOC_BASE, USER_AGENT, SocQuery
from coursepilot_ingestion.term_discovery import candidate_terms, discover, ingest_term


def _fetchers(cache_dir):
    fetcher = SocFetcher(cache_dir / "terms")

    def fetch(query: SocQuery):
        result = fetcher.fetch_courses(query, use_cache=False)   # discovery must see NOW
        return result.content, result.content_hash

    def fetch_open(query: SocQuery) -> bytes:
        url = (f"{SOC_BASE}/openSections.json?year={query.year}&term={query.term}"
               f"&campus={query.campus}")
        response = httpx.get(url, timeout=60, headers={"User-Agent": USER_AGENT},
                             follow_redirects=True)
        response.raise_for_status()
        return response.content

    return fetch, fetch_open


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="coursepilot-terms")
    p.add_argument("command", choices=("discover", "ingest", "status"))
    p.add_argument("--term", action="append", help="limit to these term codes (repeatable)")
    p.add_argument("--campus", default="NB")
    p.add_argument("--database-url")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import pathlib

    cache = pathlib.Path(args.cache_dir)
    engine = create_engine(resolve_database_url(args.database_url), future=True)
    with Session(engine) as session:
        if args.command == "discover":
            fetch, fetch_open = _fetchers(cache)
            discover(session, fetch, fetch_open=fetch_open, terms=args.term, campus=args.campus)
        elif args.command == "ingest":
            rows = session.scalars(select(RutgersTerm).where(
                RutgersTerm.campus == args.campus, RutgersTerm.publication_state == PUBLISHED))
            for row in rows:
                if args.term and row.term_code not in args.term:
                    continue
                if row.ingestion_state == INGESTED and row.ingested_hash == row.content_hash:
                    continue                  # already validated; nothing new
                archive = cache / "terms"

                def courses(q, archive=archive):
                    return CourseIngestionPipeline(session, archive).run(q, coverage="unknown")

                def sections(q, archive=archive):
                    return SectionIngestionPipeline(session, archive).run(q)

                outcome = ingest_term(session, row, run_courses=courses, run_sections=sections)
                print(f"{row.term_code}: {'ingested' if outcome.ok else 'FAILED ' + str(outcome.error)}"
                      f" {outcome.detail}")
        for row in session.scalars(select(RutgersTerm).where(RutgersTerm.campus == args.campus)
                                   .order_by(RutgersTerm.term_code)):
            if args.term and row.term_code not in args.term:
                continue
            verdict = registry_state(row)
            print(f"{row.term_code} {row.campus}: published={row.publication_state} "
                  f"courses={row.course_count} sections={row.section_count} "
                  f"ingestion={row.ingestion_state} authority={verdict.value if verdict else 'unchecked'} "
                  f"availability={'yes' if row.availability_supported else 'no'} "
                  f"disagreements={row.disagreements or {}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["candidate_terms", "main"]
