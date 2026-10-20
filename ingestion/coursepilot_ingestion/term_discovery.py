"""Rutgers term discovery, ingestion and validation (Phase 7.2.4).

Rutgers SOC has no "available terms" endpoint (init.json / subjects.json are
404 - docs/DATA_SOURCES.md), so discovery probes a ROLLING window of term
codes computed from today: the term in session, the one before it, and the
next few. Nothing is hard-coded: when Fall 2027 appears it is found exactly
as Spring 2027 was.

Three states stay distinct (models.terms):

    discovered  the public source returned a non-empty schedule   publication_state
    ingested    CoursePilot loaded it AND validated the load       ingestion_state
    ready       both - only then may it overrule history          services.term_authority

Load: one sequential request per candidate per run, a pause between
requests, archived payloads (ingestion re-reads the exact bytes discovery
saw), no concurrency. A failed request never marks a term "not published".
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.models import CourseOffering, CourseSection, DataSource, RutgersTerm
from app.models.terms import (
    CHECK_FAILED,
    INGESTED,
    INGESTION_FAILED,
    NOT_PUBLISHED,
    PUBLISHED,
    UNCHECKED,
)
from app.services.planning.calendar import current_term
from app.services.planning.terms import next_term
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from coursepilot_ingestion.sources.soc import SocQuery

logger = logging.getLogger("coursepilot.terms")

DEFAULT_AHEAD = 4          # in-session term + the next four (two regular terms ahead)
DEFAULT_BEHIND = 1


def previous_term(code: str) -> str:
    year, s = int(code[:4]), code[4]
    order = "0179"
    i = order.index(s)
    return f"{year}{order[i - 1]}" if i else f"{year - 1}{order[-1]}"


def candidate_terms(current: str, *, ahead: int = DEFAULT_AHEAD,
                    behind: int = DEFAULT_BEHIND) -> list[str]:
    """[behind..., current, ahead...] in Rutgers order (Winter, Spring, Summer, Fall)."""
    before = [current]
    for _ in range(behind):
        before.insert(0, previous_term(before[0]))
    after = [current]
    for _ in range(ahead):
        after.append(next_term(after[-1]))
    return before[:-1] + after


@dataclass
class Probe:
    """What one request established. `courses` is None when the request failed."""

    term_code: str
    courses: list | None
    content_hash: str | None = None
    error_code: str | None = None
    open_indexes: set[str] | None = None


def decode_json(content: bytes):
    return json.loads(gzip.decompress(content) if content[:2] == b"\x1f\x8b" else content)


def counts(courses: list) -> tuple[int, int]:
    return len(courses), sum(len(c.get("sections") or []) for c in courses)


def compare_sources(courses: list, open_indexes: set[str]) -> dict:
    """courses.json openStatus vs openSections.json - COUNTS of disagreement.
    Recorded for review; neither source silently overwrites the other."""
    sections = {s["index"]: s for c in courses for s in (c.get("sections") or []) if s.get("index")}
    open_in_courses = {i for i, s in sections.items() if s.get("openStatus")}
    return {
        "sections": len(sections),
        "open_sections_listed": len(open_indexes),
        "listed_open_but_unknown_section": len(open_indexes - sections.keys()),
        "open_in_courses_not_listed": len(open_in_courses - open_indexes),
        "listed_open_but_closed_in_courses": len(
            {i for i in open_indexes & sections.keys() if not sections[i].get("openStatus")}),
    }


def term_row(session: Session, term: str, campus: str) -> RutgersTerm:
    row = session.scalar(select(RutgersTerm).where(RutgersTerm.term_code == term,
                                                   RutgersTerm.campus == campus))
    if row is None:
        row = RutgersTerm(term_code=term, campus=campus, publication_state=UNCHECKED,
                          course_count=0, section_count=0, ingestion_state="not_ingested",
                          availability_supported=False, disagreements={})
        session.add(row)
    return row


def record_probe(session: Session, probe: Probe, *, campus: str, now: datetime) -> RutgersTerm:
    """The state transition for one probe. Pure apart from the row it updates."""
    row = term_row(session, probe.term_code, campus)
    row.last_checked_at = now
    if probe.courses is None:
        # A failed request proves nothing about publication.
        if row.publication_state == UNCHECKED:
            row.publication_state = CHECK_FAILED
        row.last_error = probe.error_code or "FETCH_FAILED"
        return row
    row.last_fetched_at = now
    row.content_hash = probe.content_hash
    row.course_count, row.section_count = counts(probe.courses)
    if row.section_count > 0:
        if row.publication_state != PUBLISHED:
            row.first_published_seen_at = row.first_published_seen_at or now
        row.publication_state = PUBLISHED
    else:
        row.publication_state = NOT_PUBLISHED
    row.last_error = None
    if probe.open_indexes is not None:
        row.availability_supported = True
        if row.section_count:
            row.disagreements = compare_sources(probe.courses, probe.open_indexes)
    return row


Fetch = Callable[[SocQuery], tuple[bytes, str]]       # -> (content, sha256)
FetchOpen = Callable[[SocQuery], bytes]


def probe_term(term: str, campus: str, fetch: Fetch, fetch_open: FetchOpen | None) -> Probe:
    query = SocQuery(year=int(term[:4]), term=term[4], campus=campus)
    try:
        content, digest = fetch(query)
        courses = decode_json(content)
        if not isinstance(courses, list):
            return Probe(term, None, error_code="UNEXPECTED_SHAPE")
    except Exception as exc:  # noqa: BLE001 - network/HTTP/JSON: a closed code, never the body
        logger.warning("term %s probe failed: %s", term, type(exc).__name__)
        return Probe(term, None, error_code="FETCH_FAILED")
    open_indexes = None
    if fetch_open is not None and courses:
        try:
            listed = decode_json(fetch_open(query))
            open_indexes = {str(i) for i in listed} if isinstance(listed, list) else None
        except Exception:  # noqa: BLE001 - availability is optional evidence
            open_indexes = None
    return Probe(term, courses, content_hash=digest, open_indexes=open_indexes)


def discover(session: Session, fetch: Fetch, *, fetch_open: FetchOpen | None = None,
             terms: Iterable[str] | None = None, campus: str = "NB",
             now: Callable[[], datetime] = lambda: datetime.now(UTC),
             pause: float = 2.0) -> list[RutgersTerm]:
    """Probe each candidate term once, sequentially, and record what was seen."""
    rows = []
    for i, term in enumerate(terms or candidate_terms(current_term())):
        if i and pause:
            time.sleep(pause)
        rows.append(record_probe(session, probe_term(term, campus, fetch, fetch_open),
                                 campus=campus, now=now()))
    session.commit()
    return rows


@dataclass
class IngestOutcome:
    term_code: str
    ok: bool
    error: str | None = None
    detail: dict = field(default_factory=dict)


def ingest_term(session: Session, row: RutgersTerm, *,
                run_courses: Callable[[SocQuery], object],
                run_sections: Callable[[SocQuery], object],
                now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> IngestOutcome:
    """Load a PUBLISHED term with the existing pipelines, then VALIDATE before
    calling it ingested. The loaded source row is promoted to coverage
    'complete' only after validation, so a failed or short load can never be
    mistaken for authoritative term data."""
    if row.publication_state != PUBLISHED or not row.content_hash:
        return IngestOutcome(row.term_code, False, "NOT_PUBLISHED")
    query = SocQuery(year=int(row.term_code[:4]), term=row.term_code[4], campus=row.campus)
    try:
        course_stats = run_courses(query)
        section_stats = run_sections(query)
    except Exception as exc:  # noqa: BLE001 - any pipeline failure = not ingested
        session.rollback()
        return _failed(session, row, "PIPELINE_ERROR", {"exception": type(exc).__name__})
    detail = {"courses_validated": course_stats.validated,
              "sections_validated": section_stats.validated}
    error = None
    if course_stats.source_content_hash != row.content_hash or \
            section_stats.source_content_hash != row.content_hash:
        error = "PAYLOAD_CHANGED"            # not the payload discovery saw
    elif course_stats.parse_failed or course_stats.validation_failed or \
            section_stats.parse_failed or section_stats.validation_failed:
        error = "RECORDS_REJECTED"
    else:
        offerings = session.scalar(select(func.count()).select_from(CourseOffering)
                                   .where(CourseOffering.term_code == row.term_code))
        sections = session.scalar(select(func.count()).select_from(CourseSection)
                                  .where(CourseSection.term_code == row.term_code))
        detail |= {"offerings_in_db": offerings, "sections_in_db": sections}
        if offerings < row.course_count:
            error = "COURSE_COUNT_MISMATCH"
        elif sections < row.section_count:
            error = "SECTION_COUNT_MISMATCH"
    if error:
        return _failed(session, row, error, detail)
    source = session.scalar(select(DataSource).where(
        DataSource.kind == "rutgers_official_api", DataSource.term_code == row.term_code,
        DataSource.content_hash == row.content_hash))
    if source is not None:
        source.coverage = "complete"
        source.coverage_note = "Validated by term ingestion (Phase 7.2.4)."
    row.ingestion_state = INGESTED
    row.ingested_hash = row.content_hash
    row.last_validated_at = now()
    row.last_error = None
    session.commit()
    return IngestOutcome(row.term_code, True, detail=detail)


def _failed(session: Session, row: RutgersTerm, error: str, detail: dict) -> IngestOutcome:
    # Keep any EARLIER validated load: ingested_hash still names it, and
    # term_authority keeps an already-ready term ready.
    if row.ingestion_state != INGESTED:
        row.ingestion_state = INGESTION_FAILED
    row.last_error = error
    session.commit()
    return IngestOutcome(row.term_code, False, error, detail)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


__all__ = ["IngestOutcome", "Probe", "candidate_terms", "compare_sources", "discover",
           "ingest_term", "previous_term", "probe_term", "record_probe"]
