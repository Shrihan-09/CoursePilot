"""Refresh registration availability for one term (Phase 6.7).

Usage:
    python -m coursepilot_ingestion.availability_cli --year 2026 --term 9
    python -m coursepilot_ingestion.availability_cli --year 2026 --term 9 \\
        --file data/raw/live/openSections_2026_9_NB_20261014T022951Z.json \\
        --observed-at 2026-10-14T02:29:51Z

ONE request (openSections.json, ~96 KB) per run, or a replay of an archived
payload with its original observation time. Separate from
`coursepilot_ingestion.cli` on purpose: it never re-ingests courses or
sections, so it can run as often as availability needs without touching the
catalog. The term's sections must already be loaded. No loop lives here -
the cadence is a deployment decision.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from coursepilot_ingestion.cli import ROOT, resolve_database_url
from coursepilot_ingestion.pipelines.availability import (
    AvailabilityRefreshPipeline,
    from_archive,
)
from coursepilot_ingestion.sources.soc import SocQuery

DEFAULT_ARCHIVE = ROOT / "data" / "raw" / "live"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="coursepilot-availability",
        description="Record one openSections.json observation for one term.")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--term", required=True, help="9=Fall 1=Spring 7=Summer 0=Winter")
    p.add_argument("--campus", default="NB",
                   help="sent as published; Rutgers ignores it (the list is term-wide)")
    p.add_argument("--file", default=None, help="replay an archived payload instead of fetching")
    p.add_argument("--observed-at", default=None,
                   help="ISO time the archived --file was fetched (required with --file)")
    p.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE))
    p.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL_SYNC")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)-7s %(name)s: %(message)s")
    if args.file and not args.observed_at:
        raise SystemExit("--file needs --observed-at: an archive's own time is never 'now'.")
    query = SocQuery(year=args.year, term=args.term, campus=args.campus)
    engine = create_engine(resolve_database_url(args.database_url), future=True)
    with Session(engine) as session:
        pipeline = AvailabilityRefreshPipeline(session, pathlib.Path(args.archive_dir))
        fetched = None
        if args.file:
            observed = datetime.fromisoformat(args.observed_at)
            fetched = from_archive(pathlib.Path(args.file), pipeline.url(query), observed)
        refresh = pipeline.run(query, fetched=fetched)
        print(f"term {refresh.term_code}  observed {refresh.observed_at.isoformat()}  "
              f"verdict {refresh.verdict}")
        print(f"  entries {refresh.entries}  duplicates {refresh.duplicates}  "
              f"matched {refresh.matched}  ignored {refresh.ignored}  "
              f"transitions {refresh.transitions}")
        if refresh.detail:
            print(f"  detail  {refresh.detail}")
    return 0 if refresh.verdict == "accepted" else 2


if __name__ == "__main__":
    sys.exit(main())
