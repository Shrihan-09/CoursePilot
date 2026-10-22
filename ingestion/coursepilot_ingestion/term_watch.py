"""Term watcher (Phase 7.3): load each Rutgers term as soon as SOC publishes it.

A thin layer over Phase 7.2.4 term discovery/ingestion (term_discovery.py) -
not a second system. Per run, for each candidate term (computed from today
with planning/terms.next_term; nothing hard-coded):

    classify        published | empty_unpublished | fetch_failed
                    (HTTP 200 + [] is NEVER "failed" and never "all closed")
    new term        structural check vs a baseline term -> full load (courses,
                    prerequisites, sections) -> availability
    loaded term     re-fetch DAILY before classes start, WEEKLY after; a changed
                    payload is a new data_source row (versioned); sections
                    missing from it follow the Phase 6.8 staleness rule
                    (services.monitoring.removed_section_ids) - never deleted
    coverage        'partial' -> 'complete' ONLY with stated evidence: the same
                    course and section counts across STABLE_FETCHES successful
                    fetches spanning at least STABLE_SPAN

At most ONE courses.json request per term per run. Idempotent: an unchanged
payload writes no academic data (only the watcher's own bookkeeping:
last-checked time and the bounded observation list).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from app.models import DataSource, RutgersTerm
from app.models.terms import CHECK_FAILED, INGESTED, NOT_PUBLISHED, PUBLISHED
from app.services.planning.calendar import (
    _FALL_START,
    _SPRING_START,
    _SUMMER_START,
    _WINTER_START,
    current_term,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from coursepilot_ingestion.term_discovery import (
    Fetch,
    candidate_terms,
    decode_json,
    ingest_term,
    probe_term,
    record_probe,
)
from coursepilot_ingestion.term_structure import check as structural_check

logger = logging.getLogger("coursepilot.term_watch")

STABLE_FETCHES = 3
STABLE_SPAN = timedelta(days=2)
MAX_OBSERVATIONS = 20
BEFORE_CLASSES, AFTER_CLASSES = timedelta(days=1), timedelta(days=7)


class WatchState(StrEnum):
    PUBLISHED = "published"
    EMPTY_UNPUBLISHED = "empty_unpublished"
    FETCH_FAILED = "fetch_failed"


def classify(row: RutgersTerm) -> WatchState:
    return {PUBLISHED: WatchState.PUBLISHED, NOT_PUBLISHED: WatchState.EMPTY_UNPUBLISHED,
            CHECK_FAILED: WatchState.FETCH_FAILED}.get(row.publication_state,
                                                       WatchState.FETCH_FAILED)


def term_start(term: str) -> date:
    """APPROXIMATE first day of classes (planning/calendar boundaries)."""
    year, season = int(term[:4]), term[4]
    if season == "0":
        return date(year - 1, *_WINTER_START)
    return date(year, *{"1": _SPRING_START, "7": _SUMMER_START, "9": _FALL_START}[season])


def due(row: RutgersTerm | None, now: datetime) -> bool:
    if row is None or row.last_checked_at is None:
        return True
    if row.publication_state != PUBLISHED:
        return now - _aware(row.last_checked_at) >= BEFORE_CLASSES
    cadence = BEFORE_CLASSES if now.date() < term_start(row.term_code) else AFTER_CLASSES
    return now - _aware(row.last_checked_at) >= cadence


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def promotion_evidence(row: RutgersTerm) -> str | None:
    """The stated reason to call the term COMPLETE, or None (stays partial)."""
    recent = [o for o in (row.observations or []) if o.get("sections")][-STABLE_FETCHES:]
    if len(recent) < STABLE_FETCHES:
        return None
    counts = {(o["courses"], o["sections"]) for o in recent}
    first, last = datetime.fromisoformat(recent[0]["at"]), datetime.fromisoformat(recent[-1]["at"])
    if len(counts) != 1 or last - first < STABLE_SPAN:
        return None
    courses, sections = counts.pop()
    return (f"{courses} courses / {sections} sections unchanged across {STABLE_FETCHES} fetches "
            f"{first.date()}..{last.date()}")


@dataclass
class TermOutcome:
    term_code: str
    state: str
    action: str                    # skipped_not_due | checked | loaded | reloaded | unchanged | blocked
    detail: dict = field(default_factory=dict)


Load = Callable[[Session, RutgersTerm], object]          # returns ingest_term outcome
Availability = Callable[[Session, str], object]


def watch(session: Session, fetch: Fetch, *, load_courses: Callable, load_sections: Callable,
          load_availability: Availability | None = None, baseline: Callable[[], list] | None = None,
          terms: list[str] | None = None, campus: str = "NB",
          now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> list[TermOutcome]:
    out: list[TermOutcome] = []
    for term in terms or candidate_terms(current_term()):
        row = session.scalar(select(RutgersTerm).where(RutgersTerm.term_code == term,
                                                       RutgersTerm.campus == campus))
        moment = now()
        if not due(row, moment):
            out.append(TermOutcome(term, classify(row).value, "skipped_not_due"))
            continue
        payload: dict = {}

        def capture(query, payload=payload):
            content, digest = fetch(query)            # the run's ONE courses.json request
            payload["content"] = content
            return content, digest

        previous_hash = row.ingested_hash if row is not None else None
        row = record_probe(session, probe_term(term, campus, capture, None), campus=campus,
                           now=moment)
        state = classify(row)
        if state is not WatchState.PUBLISHED:
            session.commit()
            out.append(TermOutcome(term, state.value, "checked"))
            continue
        row.observations = ([*(row.observations or []),
                             {"at": moment.isoformat(), "hash": row.content_hash,
                              "courses": row.course_count, "sections": row.section_count}]
                            )[-MAX_OBSERVATIONS:]
        if row.ingestion_state == INGESTED and row.content_hash == previous_hash:
            action = "unchanged"                      # no academic writes
        else:
            if baseline is not None and "content" in payload:
                report = structural_check(decode_json(payload["content"]), baseline())
                if not report.ok:
                    row.last_error = "STRUCTURE_CHANGED"
                    session.commit()
                    out.append(TermOutcome(term, state.value, "blocked",
                                           {"blocking": report.blocking}))
                    continue
            outcome = ingest_term(session, row, run_courses=load_courses,
                                  run_sections=load_sections, now=now)
            if not outcome.ok:
                out.append(TermOutcome(term, state.value, "failed", {"error": outcome.error}))
                continue
            if load_availability is not None:
                load_availability(session, term)
            action = "reloaded" if previous_hash else "loaded"
        evidence = promotion_evidence(row)
        if evidence and row.coverage_state != "complete" and row.ingestion_state == INGESTED:
            row.coverage_state, row.coverage_evidence = "complete", evidence
            row.coverage_promoted_at = moment
            for source in session.scalars(select(DataSource).where(
                    DataSource.kind == "rutgers_official_api", DataSource.term_code == term,
                    DataSource.content_hash == row.ingested_hash)):
                source.coverage, source.coverage_note = "complete", evidence
            action += "+promoted"
        session.commit()
        out.append(TermOutcome(term, state.value, action,
                               {"courses": row.course_count, "sections": row.section_count,
                                "coverage": row.coverage_state}))
    return out


__all__ = ["STABLE_FETCHES", "STABLE_SPAN", "TermOutcome", "WatchState", "classify", "due",
           "promotion_evidence", "term_start", "watch"]
