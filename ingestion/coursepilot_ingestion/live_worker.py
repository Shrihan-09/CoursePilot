"""The CoursePilot Live worker (Phase 6.9): the server-side loop behind every watch.

One tick:

    for each ACTIVE term in availability_poll_schedule (operator-managed):
        claim the term's lease if it is due        (short committed transaction)
        GET openSections.json                      (NO transaction open; ONE request)
        apply it IF the lease is still ours       (fenced; 6.7 apply_refresh)
        release the lease, recording outcome + duration
    process_openings   (6.8: events + inbox items - every tick, idempotent)
    fan_out            (deliveries per active endpoint)
    deliver_due        (provider sends, outside any transaction) - if configured
    expire_live_activities

The Rutgers request count depends only on the active terms and the cadence:
never on users, watches, endpoints or Live Activities. Any number of
workers may run; the lease makes exactly one poll a term at a time, and every
later step is idempotent and SKIP LOCKED-safe. A failing step is logged and
counted and never stops the other steps; the loop backs off when ticks keep
failing and stops cleanly when told to.
"""

from __future__ import annotations

import logging
import pathlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from app.services.availability import apply_refresh
from app.services.live import (
    claim_term,
    deliver_due,
    expire_live_activities,
    fan_out,
    holds_lease,
    release_term,
    schedules,
)
from app.services.monitoring import process_openings
from app.services.push_providers import PushProvider
from sqlalchemy.orm import Session

from coursepilot_ingestion.pipelines.availability import (
    AvailabilityFetch,
    AvailabilityRefreshPipeline,
)
from coursepilot_ingestion.sources.soc import SocQuery

logger = logging.getLogger("coursepilot.live")

TERM_LEASE = timedelta(seconds=120)          # > the 30 s fetch timeout + apply


def query_for(term_code: str, campus: str) -> SocQuery:
    """'20269' -> year 2026, term '9' (Rutgers YYYYT)."""
    return SocQuery(year=int(term_code[:4]), term=term_code[4:], campus=campus)


@dataclass
class TickReport:
    polled: dict[str, str] = field(default_factory=dict)       # term -> verdict / outcome
    openings: int = 0
    events: int = 0
    deliveries_created: int = 0
    delivered: int = 0
    retried: int = 0
    failed: int = 0
    suppressed: int = 0
    activities_expired: int = 0
    errors: list[str] = field(default_factory=list)            # step names, never secrets


class LiveWorker:
    def __init__(self, session_factory: Callable[[], Session], *, owner: str,
                 archive_dir: pathlib.Path, provider: PushProvider | None = None,
                 transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 lease: timedelta = TERM_LEASE, deliver_limit: int = 100,
                 stop: threading.Event | None = None) -> None:
        self.session_factory = session_factory
        self.owner = owner
        self.archive_dir = archive_dir
        self.provider = provider
        self.transport = transport
        self.clock = clock
        self.lease = lease
        self.deliver_limit = deliver_limit
        self.stop = stop or threading.Event()

    # ---------------------------------------------------------------- refresh

    def poll_term(self, term_code: str, campus: str) -> str | None:
        """Refresh one term if due and free. None: not due / held elsewhere."""
        with self.session_factory() as s:
            if claim_term(s, term_code, owner=self.owner, now=self.clock(),
                          lease=self.lease) is None:
                return None
        started = time.perf_counter()
        outcome = "error"
        try:
            query = query_for(term_code, campus)
            with self.session_factory() as s:
                pipeline = AvailabilityRefreshPipeline(s, self.archive_dir,
                                                       transport=self.transport)
                observed = self.clock()
                raw = pipeline.fetch(query)                       # no transaction open
                fetched = AvailabilityFetch(raw.url, observed, raw.body, raw.error)
                if not holds_lease(s, term_code, owner=self.owner, now=self.clock()):
                    s.rollback()
                    outcome = "lease_lost"                        # another worker took over
                    logger.warning("availability_lease_lost", extra={"term": term_code})
                    return outcome
                refresh = apply_refresh(
                    s, term_code=term_code, body=fetched.body, observed_at=fetched.observed_at,
                    source_url=fetched.url, archive_path=pipeline._archive(query, fetched),
                    fetch_error=fetched.error)
                s.commit()
                outcome = refresh.verdict
                level = logging.INFO if outcome == "accepted" else logging.WARNING
                logger.log(level, "availability_refresh", extra={
                    "term": term_code, "verdict": outcome, "transitions": refresh.transitions})
            return outcome
        except Exception as exc:  # noqa: BLE001 - the worker boundary: logged + counted
            outcome = f"error:{type(exc).__name__}"
            logger.error("availability_refresh_failed",
                         extra={"term": term_code, "error_type": type(exc).__name__})
            return outcome
        finally:
            with self.session_factory() as s:
                release_term(s, term_code, owner=self.owner, now=self.clock(), outcome=outcome,
                             duration_ms=int((time.perf_counter() - started) * 1000))

    # ---------------------------------------------------------------- one tick

    def _step(self, report: TickReport, name: str, work):
        try:
            with self.session_factory() as s:
                return work(s)
        except Exception as exc:  # noqa: BLE001 - one step never stops the others
            report.errors.append(name)
            logger.error("live_step_failed", extra={"step": name,
                                                    "error_type": type(exc).__name__})
            return None

    def tick(self) -> TickReport:
        report = TickReport()
        terms = self._step(report, "schedules", lambda s: [
            (x.term_code, x.campus) for x in schedules(s)]) or []
        for term_code, campus in terms:
            if self.stop.is_set():
                break
            outcome = self.poll_term(term_code, campus)
            if outcome is not None:
                report.polled[term_code] = outcome
                if outcome.startswith("error"):
                    report.errors.append(f"refresh:{term_code}")
        opened = self._step(report, "process_openings",
                            lambda s: process_openings(s, now=self.clock()))
        if opened is not None:
            report.openings, report.events = opened.openings, opened.events_created
        fanned = self._step(report, "fan_out", lambda s: fan_out(s, now=self.clock()))
        if fanned is not None:
            report.deliveries_created = fanned.deliveries
        if self.provider is not None:
            sent = self._step(report, "deliver", lambda s: deliver_due(
                s, self.provider, now=self.clock(), limit=self.deliver_limit))
            if sent is not None:
                report.delivered, report.retried = sent.delivered, sent.retried
                report.failed, report.suppressed = sent.failed, sent.suppressed

        def expire(s):
            count = expire_live_activities(s, now=self.clock())
            s.commit()
            return count
        report.activities_expired = self._step(report, "expire_activities", expire) or 0
        logger.info("live_tick", extra={
            "terms_polled": len(report.polled), "openings": report.openings,
            "events": report.events, "deliveries_created": report.deliveries_created,
            "delivered": report.delivered, "retried": report.retried, "failed": report.failed,
            "errors": len(report.errors)})
        return report

    # ---------------------------------------------------------------- the loop

    def run(self, *, idle: float = 5.0, max_ticks: int | None = None,
            max_backoff: float = 300.0) -> int:
        """Tick until stopped. Consecutive failing ticks back off exponentially
        (capped) instead of hot-looping; nothing is swallowed silently."""
        ticks, failing = 0, 0
        while not self.stop.is_set():
            report = self.tick()
            ticks += 1
            failing = failing + 1 if report.errors else 0
            if max_ticks is not None and ticks >= max_ticks:
                break
            wait = idle if not failing else min(max_backoff, idle * (2 ** failing))
            self.stop.wait(wait)
        return ticks


__all__ = ["TERM_LEASE", "LiveWorker", "TickReport", "query_for"]
