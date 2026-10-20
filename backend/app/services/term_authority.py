"""Target-term authority (Phase 7.2.4): may history decide an offering in this term?

The invariant this module exists for:

    IF authoritative target-term data is available and valid,
    THEN historical offering evidence MUST NOT decide whether a course is
    offered in that term.

    READY              Rutgers published it; CoursePilot loaded AND validated it.
                       History decides nothing - the term data does.
    PUBLISHED_PENDING  Rutgers publishes it; CoursePilot has not (validly) loaded it.
                       History decides nothing - the offering is UNKNOWN until loaded.
    NOT_PUBLISHED      Checked: Rutgers returns no schedule for it.
                       History may participate, labelled as history.
    UNCHECKED          CoursePilot has not checked Rutgers for it.
                       History may participate - never worded "Rutgers hasn't published".

Sources, in order: the term registry (term discovery, ingestion and
validation), then - for terms loaded before the registry existed - the
loaded SOC payload's own coverage statement ("complete" = READY). A legacy
"partial" load says nothing about publication, so it stays UNCHECKED.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataSource, RutgersTerm
from app.models.terms import INGESTED, NOT_PUBLISHED, PUBLISHED


class TermAuthority(StrEnum):
    READY = "ready"
    PUBLISHED_PENDING = "published_pending"
    NOT_PUBLISHED = "not_published"
    UNCHECKED = "unchecked"


@dataclass(frozen=True)
class TermState:
    term_code: str
    authority: TermAuthority
    #: When CoursePilot last asked Rutgers about this term (None: never / legacy).
    checked_at: datetime | None = None
    #: The validated payload fingerprint the term data rests on, when READY.
    data_version: str | None = None


def registry_state(row: RutgersTerm) -> TermAuthority | None:
    """The registry's verdict, or None when it has nothing to say (unchecked/failed check)."""
    if row.publication_state == PUBLISHED:
        if row.ingestion_state == INGESTED and row.ingested_hash and row.section_count > 0:
            return TermAuthority.READY
        return TermAuthority.PUBLISHED_PENDING
    if row.publication_state == NOT_PUBLISHED:
        return TermAuthority.NOT_PUBLISHED
    return None


def term_states(session: Session, campus: str = "NB") -> dict[str, TermState]:
    states: dict[str, TermState] = {}
    for term, coverage, content_hash in session.execute(
            select(DataSource.term_code, DataSource.coverage, DataSource.content_hash)
            .where(DataSource.kind == "rutgers_official_api", DataSource.term_code.is_not(None))):
        if coverage == "complete":
            states[term] = TermState(term, TermAuthority.READY, data_version=content_hash)
        else:
            states.setdefault(term, TermState(term, TermAuthority.UNCHECKED))
    for row in session.scalars(select(RutgersTerm).where(RutgersTerm.campus == campus)):
        verdict = registry_state(row)
        if verdict is None:
            continue
        if verdict is not TermAuthority.READY and term_is_ready(states.get(row.term_code)):
            # Already-validated term data outranks a later empty response or a
            # failed re-ingest of a newer payload: never downgrade loaded data.
            continue
        states[row.term_code] = TermState(
            row.term_code, verdict, checked_at=row.last_checked_at,
            data_version=row.ingested_hash if verdict is TermAuthority.READY else None)
    return states


def term_is_ready(state: TermState | None) -> bool:
    return state is not None and state.authority is TermAuthority.READY


def authority_of(states: dict[str, TermState], term: str) -> TermState:
    return states.get(term) or TermState(term, TermAuthority.UNCHECKED)


__all__ = ["TermAuthority", "TermState", "authority_of", "registry_state", "term_is_ready",
           "term_states"]
