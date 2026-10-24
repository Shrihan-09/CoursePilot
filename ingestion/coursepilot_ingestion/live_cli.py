"""CoursePilot Live operations (Phase 6.9).

Usage:
    python -m coursepilot_ingestion.live_cli schedule add --term 20269 [--interval 90]
    python -m coursepilot_ingestion.live_cli schedule deactivate --term 20269
    python -m coursepilot_ingestion.live_cli schedule list
    python -m coursepilot_ingestion.live_cli tick                 # one pass, then exit
    python -m coursepilot_ingestion.live_cli run [--idle 5]       # the production loop

`run` is the ONE long-lived process production needs (DATA_MODEL 45.3): it
polls each active term at its cadence, turns openings into events and inbox
items, fans out and delivers. Several may run; the per-term lease keeps
Rutgers at one request per term per interval. Stops cleanly on Ctrl+C,
SIGTERM, or SIGBREAK (Windows).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import threading
import uuid

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.models.live import AvailabilityPollSchedule
from app.services.push_providers import provider_from_settings
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from coursepilot_ingestion.cli import ROOT, resolve_database_url
from coursepilot_ingestion.live_worker import LiveWorker

TERM_PATTERN = r"^\d{4}[0179]$"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coursepilot-live", description=__doc__.split("\n")[0])
    p.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL_SYNC")
    sub = p.add_subparsers(dest="command", required=True)
    sched = sub.add_parser("schedule", help="manage which terms are polled")
    sched.add_argument("action", choices=("add", "activate", "deactivate", "list"))
    sched.add_argument("--term", help="YYYYT, e.g. 20269")
    sched.add_argument("--campus", default="NB")
    sched.add_argument("--interval", type=int, default=90, help="seconds (>= 30)")
    sched.add_argument("--backoff-cap", type=int, default=900, help="seconds")
    sub.add_parser("tick", help="one pass of the loop, then exit")
    run = sub.add_parser("run", help="the production loop")
    run.add_argument("--idle", type=float, default=5.0, help="seconds between ticks")
    run.add_argument("--max-ticks", type=int, default=None)
    return p


def _schedule(session: Session, args) -> int:
    import re
    if args.action == "list":
        for s in session.scalars(select(AvailabilityPollSchedule)
                                 .order_by(AvailabilityPollSchedule.term_code)):
            print(f"{s.term_code} {s.campus} active={s.active} every {s.interval_seconds}s "
                  f"last={s.last_outcome} at {s.last_finished_at} lease={s.lease_owner}")
        return 0
    if not args.term or not re.match(TERM_PATTERN, args.term):
        raise SystemExit("--term YYYYT is required (T in 0,1,7,9)")
    row = session.get(AvailabilityPollSchedule, args.term)
    if args.action == "add":
        if row is None:
            row = AvailabilityPollSchedule(term_code=args.term)
            session.add(row)
        row.campus, row.active = args.campus, True
        row.interval_seconds, row.backoff_cap_seconds = args.interval, args.backoff_cap
    elif row is None:
        raise SystemExit(f"{args.term} is not scheduled")
    else:
        row.active = args.action == "activate"
    session.commit()
    print(f"{args.term}: active={row.active} every {row.interval_seconds}s")
    return 0


def _worker(factory, stop: threading.Event | None = None) -> LiveWorker:
    settings = get_settings()
    owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
    from coursepilot_ingestion.term_watch_cli import default_watch

    return LiveWorker(factory, owner=owner, archive_dir=ROOT / "data" / "raw" / "live",
                      provider=provider_from_settings(settings), stop=stop,
                      term_watch=default_watch())   # Phase 7.3: one loop, not two


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(get_settings())
    engine = create_engine(resolve_database_url(args.database_url), future=True,
                           pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        if args.command == "schedule":
            with factory() as s:
                return _schedule(s, args)
        if args.command == "tick":
            report = _worker(factory).tick()
            print(f"polled {report.polled}  openings {report.openings}  events {report.events}"
                  f"  deliveries {report.deliveries_created}  delivered {report.delivered}"
                  f"  errors {report.errors}")
            return 1 if report.errors else 0
        stop = threading.Event()

        def _stop(_signum, _frame):
            stop.set()

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), _stop)
        ticks = _worker(factory, stop).run(idle=args.idle, max_ticks=args.max_ticks)
        logging.getLogger("coursepilot.live").info("live_worker_stopped",
                                                   extra={"ticks": ticks})
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
