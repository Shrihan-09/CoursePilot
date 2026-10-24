"""SECTION ranking (Phase 7.2.4): which section of an already-chosen course.

Kept apart from COURSE ranking (planning/ranking.py), which decides which
academically valid course to take. Here:

1. STRUCTURAL validity filters (published times, no conflict, the student's
   hard limits) - decided by the Schedule Engine, passed in, never re-judged.
2. Soft preferences order the valid sections: modality, campus, days, time
   window, then live availability, then professor evidence.
3. LIVE open/closed is reported and used only as a soft key: a closed section
   is still a valid section (it may reopen), and "open" never rescues an
   invalid one.
4. Professor evidence counts only with a VERIFIED identity; anything else is
   ignored, and it can never make an invalid section rank at all.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.intelligence import IdentityMatch
from app.domain.modality import Modality


class LiveStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class SectionCandidate(BaseModel):
    index: str
    #: The Schedule Engine's structural verdict; ranking never overrides it.
    structurally_valid: bool
    invalid_reason: str | None = None
    modality: Modality = Modality.UNKNOWN
    campus: str | None = None
    days: list[str] = Field(default_factory=list)
    start_minute: int | None = None
    end_minute: int | None = None
    live: LiveStatus = LiveStatus.UNKNOWN
    instructor_identity: IdentityMatch = IdentityMatch.NONE
    #: A preference score from VERIFIED professor evidence (higher = preferred).
    professor_preference: int = 0


class SectionPreferences(BaseModel):
    modality: Modality | None = None
    campus: str | None = None
    avoid_days: list[str] = Field(default_factory=list)
    earliest_minute: int | None = None
    latest_minute: int | None = None


class RankedSection(BaseModel):
    index: str
    preference_misses: list[str]
    live: LiveStatus
    professor_evidence_used: bool


class SectionRanking(BaseModel):
    ranked: list[RankedSection]
    excluded: dict[str, str]          # index -> structural reason (never ranked)


def _misses(c: SectionCandidate, p: SectionPreferences) -> list[str]:
    misses = []
    if p.modality is not None and c.modality is not p.modality:
        misses.append("modality")
    if p.campus is not None and c.campus != p.campus:
        misses.append("campus")
    if set(c.days) & set(p.avoid_days):
        misses.append("days")
    if p.earliest_minute is not None and c.start_minute is not None \
            and c.start_minute < p.earliest_minute:
        misses.append("too_early")
    if p.latest_minute is not None and c.end_minute is not None and c.end_minute > p.latest_minute:
        misses.append("too_late")
    return misses


def rank_sections(candidates: list[SectionCandidate],
                  preferences: SectionPreferences) -> SectionRanking:
    excluded = {c.index: c.invalid_reason or "structurally_invalid"
                for c in candidates if not c.structurally_valid}
    rows = []
    for c in candidates:
        if not c.structurally_valid:
            continue
        verified = c.instructor_identity is IdentityMatch.VERIFIED
        misses = _misses(c, preferences)
        key = (len(misses),
               {LiveStatus.OPEN: 0, LiveStatus.UNKNOWN: 1, LiveStatus.CLOSED: 2}[c.live],
               -(c.professor_preference if verified else 0),
               c.index)
        rows.append((key, RankedSection(index=c.index, preference_misses=misses, live=c.live,
                                        professor_evidence_used=verified
                                        and c.professor_preference != 0)))
    return SectionRanking(ranked=[r for _, r in sorted(rows, key=lambda kv: kv[0])],
                          excluded=excluded)


__all__ = ["LiveStatus", "RankedSection", "SectionCandidate", "SectionPreferences",
           "SectionRanking", "rank_sections"]
