"""Registration availability (Phase 6.7) - pure rules, no I/O.

Evidence: docs/investigations/phase-6-7-live-availability.md.

## What Rutgers says, and what it does not

SOC exposes ONE availability fact per section: open or closed
(`courses.json` `openStatus`, and membership in `openSections.json`, which
were identical on all 12,012 Fall 2026 NB sections at the same moment).
There are no seat counts, no capacity, no waitlist and no source timestamp.
So there are three states, and the third is CoursePilot's own:

    OPEN      Rutgers listed the section as open when observed
    CLOSED    Rutgers listed it as closed (or, for a known section of the
              term, omitted it from an ACCEPTED openSections payload)
    UNKNOWN   not observed, observation rejected, or sources disagree

Availability is NOT eligibility (may the student take the course) and NOT
structural validity (does the schedule fit). A closed section is still a
valid section; an open section does not make a student eligible.

## openSections.json is term-wide and its empty answer is ambiguous

The `campus` parameter is ignored (NK == NB byte for byte); other campuses'
indexes are ignored by joining on the term's known sections. An unpublished
term, a nonsense year and a nonsense campus all return `[]` - so an empty
payload, or one that shares no index with the term, is REJECTED: it can
never turn every section CLOSED.

## The body carries no term

openSections.json is a bare list of indexes, and indexes are REUSED across
terms (the real Spring 2026 payload shares 116 open indexes with the 498
Fall 2026 fixture sections). So content alone cannot always prove a payload
belongs to the term; the request URL's year/term must match the term it is
applied to (`term_of_url`), and the no-overlap and implausible-drop guards
catch the rest that content can show.

## Freshness

Rutgers publishes no "last updated" time, so every observation's time is
CoursePilot's own fetch time. Freshness is judged against an explicit
`reference_time` (never an implicit clock), so identical inputs give
identical answers. Only an accepted openSections refresh can be CURRENT: a
courses.json download's openStatus is kept as evidence with its time, but
that time is an archive file time (a copy or backfill resets it), so it is
never presented as "now".
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import parse_qs, urlsplit

#: Rutgers registration index: 5 digits in every observed payload.
INDEX_RE = re.compile(r"^\d{5}$")

#: CoursePilot settings, not Rutgers policy.
CURRENT_WINDOW = timedelta(minutes=10)        # an observation younger than this is "current"
CONFLICT_WINDOW = timedelta(seconds=900)      # courses.json is cacheable for 15 minutes
MIN_RETAINED_FRACTION = 0.10                  # an open count collapsing below 10% is suspect


class AvailabilityStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class Freshness(StrEnum):
    CURRENT = "current"            # observed within CURRENT_WINDOW of the reference time
    STALE = "stale"                # an older observation - shown with its time, never as "now"
    UNOBSERVED = "unobserved"      # no observation at all


class RefreshVerdict(StrEnum):
    ACCEPTED = "accepted"
    MALFORMED = "rejected_malformed"
    EMPTY = "rejected_empty"
    NO_OVERLAP = "rejected_no_overlap_with_term"
    IMPLAUSIBLE_DROP = "rejected_implausible_drop"
    TERM_MISMATCH = "rejected_term_mismatch"


class MalformedSource(ValueError):
    pass


@dataclass(frozen=True)
class OpenSectionsPayload:
    indexes: frozenset[str]
    entries: int
    duplicates: int


def parse_open_sections(body: bytes) -> OpenSectionsPayload:
    """Strict: anything other than a JSON array of 5-digit strings is malformed."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MalformedSource(f"not JSON: {exc}") from None
    if not isinstance(data, list):
        raise MalformedSource(f"expected a JSON array, got {type(data).__name__}")
    bad = [x for x in data if not isinstance(x, str) or not INDEX_RE.match(x)]
    if bad:
        raise MalformedSource(f"{len(bad)} entries are not 5-digit index strings, e.g. {bad[:3]!r}")
    unique = frozenset(data)
    return OpenSectionsPayload(unique, len(data), len(data) - len(unique))


def term_of_url(url: str) -> str | None:
    """'YYYYT' from an openSections/courses URL's year and term, if both are present."""
    q = parse_qs(urlsplit(url).query)
    year, term = q.get("year", [None])[0], q.get("term", [None])[0]
    return f"{year}{term}" if year and term else None


@dataclass(frozen=True)
class RefreshDecision:
    verdict: RefreshVerdict
    statuses: dict[str, AvailabilityStatus] = field(default_factory=dict)   # term index -> status
    matched: int = 0           # listed indexes that are sections of this term
    ignored: int = 0           # listed indexes of other campuses / unknown sections
    detail: str | None = None


def decide_refresh(payload: OpenSectionsPayload, term_indexes: Iterable[str], *,
                   previous_open: int | None = None) -> RefreshDecision:
    """Turn an openSections payload into per-section states for one term, or reject it."""
    known = frozenset(term_indexes)
    if not payload.indexes:
        return RefreshDecision(RefreshVerdict.EMPTY,
                               detail="empty list: unpublished term, bad parameters or outage")
    matched = payload.indexes & known
    ignored = len(payload.indexes) - len(matched)
    if known and not matched:
        return RefreshDecision(RefreshVerdict.NO_OVERLAP, ignored=ignored,
                               detail="no listed index belongs to this term's sections")
    if (previous_open and previous_open >= 100
            and len(matched) < previous_open * MIN_RETAINED_FRACTION):
        return RefreshDecision(RefreshVerdict.IMPLAUSIBLE_DROP, matched=len(matched),
                               ignored=ignored,
                               detail=f"open sections fell from {previous_open} to {len(matched)}")
    statuses = {i: AvailabilityStatus.OPEN if i in matched else AvailabilityStatus.CLOSED
                for i in sorted(known)}
    return RefreshDecision(RefreshVerdict.ACCEPTED, statuses, len(matched), ignored)


@dataclass(frozen=True)
class Transition:
    index: str
    from_status: AvailabilityStatus | None      # None: first observation of the section
    to_status: AvailabilityStatus


def transitions(previous: Mapping[str, AvailabilityStatus],
                current: Mapping[str, AvailabilityStatus]) -> list[Transition]:
    """State changes only: OPEN -> OPEN is not a change. Deterministic order."""
    return [Transition(i, previous.get(i), s) for i, s in sorted(current.items())
            if previous.get(i) is not s]


@dataclass(frozen=True)
class Observation:
    status: AvailabilityStatus
    observed_at: datetime
    source: str                                  # openSections.json | courses.json
    source_hash: str | None = None
    #: Only the availability refresh can make a state CURRENT. A courses.json
    #: download is a structural snapshot: its time is a file time that a copy
    #: or backfill resets, so its openStatus is evidence, never "now".
    can_be_current: bool = True


@dataclass(frozen=True)
class ResolvedAvailability:
    status: AvailabilityStatus
    freshness: Freshness
    observed_at: datetime | None
    source: str | None
    source_hash: str | None = None
    since: datetime | None = None                # when the current state was first observed
    reasons: tuple[str, ...] = ()


def resolve(observations: Iterable[Observation], reference_time: datetime, *,
            since: datetime | None = None) -> ResolvedAvailability:
    """The newest observation wins; two observations within CONFLICT_WINDOW
    that disagree make the section UNKNOWN (the conflict is reported)."""
    obs = sorted(observations, key=lambda o: (o.observed_at, o.source))
    if not obs:
        return ResolvedAvailability(AvailabilityStatus.UNKNOWN, Freshness.UNOBSERVED, None, None,
                                    reasons=("not_observed",))
    newest = obs[-1]
    fresh = (Freshness.CURRENT if newest.can_be_current
             and reference_time - newest.observed_at <= CURRENT_WINDOW else Freshness.STALE)
    notes = () if newest.can_be_current else ("structural_snapshot_not_an_availability_refresh",)
    rivals = [o for o in obs[:-1] if newest.observed_at - o.observed_at <= CONFLICT_WINDOW
              and o.status is not newest.status]
    if rivals:
        r = rivals[-1]
        return ResolvedAvailability(
            AvailabilityStatus.UNKNOWN, fresh, newest.observed_at, newest.source,
            newest.source_hash,
            reasons=(f"sources_disagree:{r.source}={r.status.value},"
                     f"{newest.source}={newest.status.value}", *notes))
    return ResolvedAvailability(newest.status, fresh, newest.observed_at, newest.source,
                                newest.source_hash,
                                since=since if newest.can_be_current else None, reasons=notes)


def bundle_status(statuses: Iterable[AvailabilityStatus]) -> AvailabilityStatus:
    """A registration bundle (lecture index + required LB lab index ...) is
    OPEN only if EVERY required component is OPEN; one CLOSED component makes
    it CLOSED; otherwise any UNKNOWN component makes it UNKNOWN."""
    values = list(statuses)
    if not values:
        return AvailabilityStatus.UNKNOWN
    if AvailabilityStatus.CLOSED in values:
        return AvailabilityStatus.CLOSED
    if AvailabilityStatus.UNKNOWN in values:
        return AvailabilityStatus.UNKNOWN
    return AvailabilityStatus.OPEN


_FRESHNESS_ORDER = (Freshness.CURRENT, Freshness.STALE, Freshness.UNOBSERVED)


def weakest(values: Iterable[Freshness]) -> Freshness:
    """A bundle is only as fresh as its least fresh component."""
    present = list(values)
    return max(present, key=_FRESHNESS_ORDER.index) if present else Freshness.UNOBSERVED


def oldest(times: Iterable[datetime | None]) -> datetime | None:
    present = [t for t in times if t is not None]
    return min(present) if present else None


__all__ = ["CONFLICT_WINDOW", "CURRENT_WINDOW", "INDEX_RE", "MIN_RETAINED_FRACTION",
           "AvailabilityStatus", "Freshness", "MalformedSource", "Observation",
           "OpenSectionsPayload", "RefreshDecision", "RefreshVerdict", "ResolvedAvailability",
           "Transition", "bundle_status", "decide_refresh", "oldest", "parse_open_sections",
           "resolve", "term_of_url", "transitions", "weakest"]
