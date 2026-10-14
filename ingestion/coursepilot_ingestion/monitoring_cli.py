"""Section monitoring operations (Phase 6.8) - the server-side half of a watch.

Usage:
    python -m coursepilot_ingestion.monitoring_cli process
    python -m coursepilot_ingestion.monitoring_cli reconcile-removed
    python -m coursepilot_ingestion.monitoring_cli expire-term --term 20269

`process` turns every pending closed -> open change into notification
events. The availability refresh already runs it after each accepted poll;
running it alone recovers anything a crash left pending (it is idempotent,
and safe beside another worker). `reconcile-removed` deactivates watches
whose section left Rutgers' schedule; `expire-term` ends a term's
monitoring - both keep the watches as history.

Nothing here contacts Rutgers or any user device: it reads what the global
refresh stored and writes events. Delivery is a later phase.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime

from app.services.monitoring import (
    deactivate_removed_targets,
    expire_term,
    pending_openings,
    process_openings,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from coursepilot_ingestion.cli import resolve_database_url


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coursepilot-monitoring",
                                description="Process watch events; end or reconcile watches.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("process", help="create events for every pending closed -> open change")
    sub.add_parser("reconcile-removed", help="deactivate watches on removed sections")
    expire = sub.add_parser("expire-term", help="end monitoring for a term (keeps history)")
    expire.add_argument("--term", required=True, help="YYYYT, e.g. 20269")
    p.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL_SYNC")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    engine = create_engine(resolve_database_url(args.database_url), future=True)
    now = datetime.now(UTC)
    with Session(engine) as session:
        if args.command == "process":
            stats = process_openings(session, now=now)
            print(f"openings {stats.openings}  matches {stats.matches}  "
                  f"events created {stats.events_created}  batches {stats.batches}  "
                  f"still pending {pending_openings(session)}")
        elif args.command == "reconcile-removed":
            count = deactivate_removed_targets(session, now=now)
            session.commit()
            print(f"watches deactivated (target_removed): {count}")
        else:
            count = expire_term(session, args.term, now=now)
            session.commit()
            print(f"watches deactivated (term_ended) for {args.term}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
