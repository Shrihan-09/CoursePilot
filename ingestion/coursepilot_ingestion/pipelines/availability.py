"""Availability refresh pipeline (Phase 6.7): one openSections.json request per term.

Its own refresh boundary - it never re-ingests the catalog, requirements,
Core, prerequisites or sections, and never writes structural rows. It needs
the term's sections to exist (loaded by the section pipeline), because
openSections.json is term-wide across campuses and is joined by index.

    GET https://classes.rutgers.edu/soc/api/openSections.json?year=Y&term=T&campus=C
        ~96 KB JSON (28 KB gzip), Cache-Control: max-age=30

Every payload is archived as received; every run records a refresh row -
accepted, or rejected with the reason (malformed, empty, wrong term,
implausible drop, fetch failure). No polling loop lives here: cadence is a
deployment decision (Rutgers' max-age=30 is the floor; 60-120 s during
registration windows was suggested in Phase 6.1, pending human sign-off).
"""

from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from app.models.availability import AvailabilityRefresh
from app.services.availability import apply_refresh
from app.services.monitoring import process_openings
from sqlalchemy.orm import Session

from coursepilot_ingestion.sources.soc import SOC_BASE, USER_AGENT, SocQuery

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AvailabilityFetch:
    url: str
    observed_at: datetime
    body: bytes | None
    error: str | None = None


class AvailabilityRefreshPipeline:
    def __init__(self, session: Session, archive_dir: pathlib.Path, *,
                 timeout: float = 30.0, user_agent: str = USER_AGENT,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.session = session
        self.archive_dir = archive_dir
        self.timeout = timeout
        self.user_agent = user_agent
        self.transport = transport            # tests inject a MockTransport: no live network

    @staticmethod
    def url(query: SocQuery) -> str:
        return (f"{SOC_BASE}/openSections.json?year={query.year}&term={query.term}"
                f"&campus={query.campus}")

    def fetch(self, query: SocQuery) -> AvailabilityFetch:
        url = self.url(query)
        observed_at = datetime.now(UTC)
        try:
            with httpx.Client(timeout=self.timeout, headers={"User-Agent": self.user_agent},
                              transport=self.transport) as client:
                response = client.get(url)
            if response.status_code != 200:
                return AvailabilityFetch(url, observed_at, None, f"HTTP {response.status_code}")
            return AvailabilityFetch(url, observed_at, response.content)
        except httpx.HTTPError as exc:
            return AvailabilityFetch(url, observed_at, None, f"{type(exc).__name__}: {exc}")

    def _archive(self, query: SocQuery, fetched: AvailabilityFetch) -> str | None:
        if fetched.body is None:
            return None
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = fetched.observed_at.strftime("%Y%m%dT%H%M%SZ")
        path = self.archive_dir / (f"openSections_{query.year}_{query.term}_{query.campus}"
                                   f"_{stamp}.json")
        path.write_bytes(fetched.body)
        return str(path)

    def run(self, query: SocQuery, *, fetched: AvailabilityFetch | None = None,
            commit: bool = True, process_events: bool = True) -> AvailabilityRefresh:
        """Fetch (or use an already-fetched/archived payload) and apply it.

        Phase 6.8: once the refresh is COMMITTED, its closed -> open changes
        are turned into watch events in separate transactions. A crash in
        between loses nothing - the changes stay pending for the next run or
        `monitoring_cli process`. The number of watches never changes the
        request above: one per term per refresh."""
        fetched = fetched or self.fetch(query)
        refresh = apply_refresh(
            self.session, term_code=query.term_code, body=fetched.body,
            observed_at=fetched.observed_at, source_url=fetched.url,
            archive_path=self._archive(query, fetched), fetch_error=fetched.error)
        if commit:
            self.session.commit()
        logger.info("availability refresh %s: %s matched=%s ignored=%s transitions=%s",
                    query.term_code, refresh.verdict, refresh.matched, refresh.ignored,
                    refresh.transitions)
        if commit and process_events and refresh.transitions:
            stats = process_openings(self.session, now=datetime.now(UTC))
            logger.info("watch events %s: openings=%s events=%s", query.term_code,
                        stats.openings, stats.events_created)
        return refresh


def from_archive(path: pathlib.Path, url: str, observed_at: datetime) -> AvailabilityFetch:
    """Replay an archived payload (fixtures, backfill) with its original observation time."""
    return AvailabilityFetch(url, observed_at, path.read_bytes())


__all__ = ["AvailabilityFetch", "AvailabilityRefreshPipeline", "from_archive"]
