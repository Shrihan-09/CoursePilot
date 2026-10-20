"""External professor-intelligence sources and their ACCESS status (Phase 7.2.4).

Findings (investigated 2026-10-19; see docs/SOURCE_AUTHORITY.md):

- Rate My Professors: its Terms of Use prohibit automated access ("scripts,
  robots ... scrape, crawl or spider") and compiling others' material without
  prior written permission; no official API or licence was found.
- Reddit: Data API access requires an explicit request and approval; scraping
  around it is prohibited; content may not train models without permission
  (secondary sources - Reddit's own pages refused automated fetches; verify).
- Rutgers SIRS: viewing depends on Rutgers-granted NetID permissions and its
  terms forbid aggregating or sharing results without Rutgers' consent.

So no adapter is registered. A source becomes usable only by changing its
access to PERMITTED with a recorded basis AND registering an adapter that
reads PRE-PROCESSED, cached summaries - never per-request fetching.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.intelligence import ExternalSource, ProfessorSignal, SourceAccess

SOURCES: dict[str, ExternalSource] = {
    "ratemyprofessors": ExternalSource(
        source="ratemyprofessors", access=SourceAccess.NOT_PERMITTED,
        basis="Terms prohibit automated access and reuse without prior written permission."),
    "reddit": ExternalSource(
        source="reddit", access=SourceAccess.PENDING_APPROVAL,
        basis="Data API requires an approved application; no scraping."),
    "rutgers_sirs": ExternalSource(
        source="rutgers_sirs", access=SourceAccess.NOT_PERMITTED,
        basis="NetID-permissioned; aggregation needs Rutgers' written consent."),
}


class ProfessorSource(Protocol):
    """An adapter over CACHED, permitted summaries for one external source."""

    source: str

    def signals(self, instructor_key: str, external_id: str) -> list[ProfessorSignal]: ...


_ADAPTERS: dict[str, ProfessorSource] = {}


class SourceNotPermitted(PermissionError):
    pass


def register(adapter: ProfessorSource) -> None:
    status = SOURCES.get(adapter.source)
    if status is None or status.access is not SourceAccess.PERMITTED:
        raise SourceNotPermitted(f"{adapter.source} is not permitted for automated use")
    _ADAPTERS[adapter.source] = adapter


def usable_sources() -> list[str]:
    return sorted(s for s in _ADAPTERS if SOURCES[s].access is SourceAccess.PERMITTED)


__all__ = ["SOURCES", "ProfessorSource", "SourceNotPermitted", "register", "usable_sources"]
