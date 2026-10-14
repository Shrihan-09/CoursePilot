"""Availability refresh and lookup (Phase 6.7).

Two operations with different boundaries:

* `apply_refresh` - WRITES. Applies one `openSections.json` payload for one
  term: a refresh row always (accepted or why rejected), and - only when
  accepted - the sections whose state CHANGED (snapshot + transition rows).
  Never touches course, offering, section, meeting, requirement or
  eligibility rows; never needs the catalog re-ingested. A fixed number of
  queries whatever the section count.

* `lookup` - READS. Current availability for many sections, from what is
  already stored: the latest accepted refresh of the term and, as a second
  source, the `openStatus` of the last `courses.json` download. Never calls
  the network - schedule generation must not fetch per candidate (or at all).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.domain.availability import (
    AvailabilityStatus,
    MalformedSource,
    Observation,
    RefreshVerdict,
    ResolvedAvailability,
    decide_refresh,
    parse_open_sections,
    resolve,
    term_of_url,
    transitions,
)
from app.models import CourseSection, DataSource
from app.models.availability import (
    AvailabilityRefresh,
    SectionAvailability,
    SectionAvailabilityChange,
)

OPEN_SECTIONS_SOURCE = "openSections.json"
COURSES_SOURCE = "courses.json"


def _latest_accepted(session: Session, term_code: str,
                     excluding: uuid.UUID | None = None) -> AvailabilityRefresh | None:
    query = (select(AvailabilityRefresh)
             .where(AvailabilityRefresh.term_code == term_code,
                    AvailabilityRefresh.verdict == RefreshVerdict.ACCEPTED.value)
             .order_by(AvailabilityRefresh.observed_at.desc(), AvailabilityRefresh.id)
             .limit(1))
    if excluding is not None:
        # the refresh being decided is already in the session (autoflush)
        query = query.where(AvailabilityRefresh.id != excluding)
    return session.scalar(query)


def apply_refresh(session: Session, *, term_code: str, body: bytes | None, observed_at: datetime,
                  source_url: str, archive_path: str | None = None,
                  fetch_error: str | None = None) -> AvailabilityRefresh:
    """Record one openSections observation and apply it if it is trustworthy.

    The caller owns the transaction. Re-applying an identical payload records
    a new refresh (the "as of" time moves) and zero transitions.

    Phase 6.8: refreshes of one term are serialized (a PostgreSQL
    transaction-scoped advisory lock; SQLite allows one writer anyway).
    Without it two concurrent refreshes both read the old snapshot and both
    record the same closed -> open - and every watcher would get two events.
    """
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                        {"key": f"availability_refresh:{term_code}"})
    refresh = AvailabilityRefresh(
        id=uuid.uuid4(), term_code=term_code, source_url=source_url, observed_at=observed_at,
        content_sha256=hashlib.sha256(body).hexdigest() if body is not None else None,
        body_bytes=len(body) if body is not None else None, archive_path=archive_path,
        verdict=RefreshVerdict.ACCEPTED.value)
    session.add(refresh)
    if body is None:
        refresh.verdict, refresh.detail = "rejected_fetch_failed", fetch_error
        session.flush()
        return refresh
    url_term = term_of_url(source_url)
    if url_term is not None and url_term != term_code:
        refresh.verdict = RefreshVerdict.TERM_MISMATCH.value
        refresh.detail = f"payload requested for term {url_term}, applied to {term_code}"
        session.flush()
        return refresh
    try:
        payload = parse_open_sections(body)
    except MalformedSource as exc:
        refresh.verdict, refresh.detail = RefreshVerdict.MALFORMED.value, str(exc)[:500]
        session.flush()
        return refresh
    refresh.entries, refresh.duplicates = payload.entries, payload.duplicates

    sections = dict(session.execute(
        select(CourseSection.index_number, CourseSection.id)
        .where(CourseSection.term_code == term_code)).all())
    current = {row.index_number: row for row in session.scalars(
        select(SectionAvailability).where(SectionAvailability.term_code == term_code))}
    previous = _latest_accepted(session, term_code, excluding=refresh.id)
    decision = decide_refresh(payload, sections,
                              previous_open=previous.matched if previous else None)
    refresh.verdict, refresh.detail = decision.verdict.value, decision.detail
    refresh.matched, refresh.ignored = decision.matched, decision.ignored
    if decision.verdict is not RefreshVerdict.ACCEPTED:
        session.flush()
        return refresh

    before = {i: AvailabilityStatus(r.status) for i, r in current.items()}
    changes = transitions(before, decision.statuses)
    refresh.transitions = len(changes)
    session.flush()                                   # the refresh row exists for the FKs
    for t in changes:
        session.add(SectionAvailabilityChange(
            section_id=sections[t.index], term_code=term_code, index_number=t.index,
            from_status=t.from_status.value if t.from_status else None,
            to_status=t.to_status.value, observed_at=observed_at, refresh_id=refresh.id))
        row = current.get(t.index)
        if row is None:
            session.add(SectionAvailability(
                section_id=sections[t.index], term_code=term_code, index_number=t.index,
                status=t.to_status.value, since=observed_at, changed_by_refresh_id=refresh.id))
        else:
            row.status, row.since, row.changed_by_refresh_id = (
                t.to_status.value, observed_at, refresh.id)
    session.flush()
    return refresh


@dataclass(frozen=True)
class SectionRef:
    """What lookup needs about a section (already loaded by the caller)."""

    section_id: uuid.UUID
    index_number: str
    open_status: bool              # courses.json openStatus at its download
    source_id: uuid.UUID           # the courses.json download it came from


def lookup(session: Session, term_code: str, refs: Iterable[SectionRef],
           reference_time: datetime, *, downloads: dict | None = None,
           ) -> tuple[dict[str, ResolvedAvailability], dict]:
    """Resolved availability per index, in at most three queries whatever the count.

    `downloads` (DataSource id -> retrieved_at) may be passed by a caller that
    already loaded them. Returns (by_index, provenance) where provenance
    names the refresh used.
    """
    refs = list(refs)
    ids = [r.section_id for r in refs]
    latest = _latest_accepted(session, term_code)
    snapshot = {}
    if ids and latest is not None:
        snapshot = {row.section_id: row for row in session.scalars(
            select(SectionAvailability).where(SectionAvailability.section_id.in_(ids)))}
    if downloads is None:
        downloads = dict(session.execute(
            select(DataSource.id, DataSource.retrieved_at)
            .where(DataSource.id.in_({r.source_id for r in refs}))).all()) if refs else {}
    out = {}
    for r in refs:
        observations = []
        retrieved = downloads.get(r.source_id)
        if retrieved is not None:
            observations.append(Observation(
                AvailabilityStatus.OPEN if r.open_status else AvailabilityStatus.CLOSED,
                _aware(retrieved), COURSES_SOURCE, can_be_current=False))
        snap = snapshot.get(r.section_id)
        if snap is not None:
            observations.append(Observation(
                AvailabilityStatus(snap.status), _aware(latest.observed_at), OPEN_SECTIONS_SOURCE,
                (latest.content_sha256 or "")[:16]))
        out[r.index_number] = resolve(observations, reference_time,
                                      since=_aware(snap.since) if snap else None)
    provenance = {"refresh_id": str(latest.id) if latest else None,
                  "refresh_observed_at": _aware(latest.observed_at) if latest else None,
                  "refresh_sha256": (latest.content_sha256 or "")[:16] if latest else None}
    return out, provenance


def refresh_count(session: Session, term_code: str) -> int:
    return session.scalar(select(func.count()).select_from(AvailabilityRefresh)
                          .where(AvailabilityRefresh.term_code == term_code)) or 0


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; every stored time is UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


__all__ = ["COURSES_SOURCE", "OPEN_SECTIONS_SOURCE", "SectionRef", "apply_refresh", "lookup",
           "refresh_count"]
