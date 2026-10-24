"""Daily course demand rollup (Phase 7.3 demand collection; DATA_MODEL 50).

Collect only: this module turns the availability change log into one
`course_demand_snapshot` row per course per term per America/New_York day.
Nothing ranks, labels or shows these rows yet.

Inputs are ONLY what the live worker already recorded - `availability_refresh`
(accepted polls) and `section_availability_change` (one row per open/closed
transition) - plus the term's sections. No Rutgers request is made here.

Honesty rules:
* A day with no accepted refresh is `insufficient_data` for every course
  (reason `no_accepted_refresh_that_day`); a course none of whose sections
  has an observed open/closed state is `insufficient_data`
  (`no_section_observed`). Measured columns are then NULL - never imputed.
* A first-closed time is the refresh that FIRST SAW the section closed;
  `not_before` is the accepted refresh before it, so the true moment lies in
  (not_before, observed_at]. A section first seen already closed after the
  window opened is `closed_when_first_observed` with no time.
* Re-running a day replaces that day's rows with identical values (upsert on
  term, course, day): the rollup is idempotent.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.registration_windows import EASTERN, first_window
from app.models import (
    AvailabilityRefresh,
    Course,
    CourseDemandSnapshot,
    CourseOffering,
    CourseSection,
    SectionAvailabilityChange,
)
from app.models.live import AvailabilityPollSchedule

#: How far back the worker fills days it has not rolled up (a laptop that slept
#: for a week still gets its days - each marked by what was actually observed).
LOOKBACK_DAYS = 14


@dataclass
class DayRollup:
    term_code: str
    day: date
    ok: int = 0
    insufficient: int = 0
    reasons: dict[str, int] = field(default_factory=dict)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """[start, end) of an Eastern calendar day, in UTC (DST-correct)."""
    start = datetime.combine(day, time(0), tzinfo=EASTERN)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=EASTERN)
    return start.astimezone(UTC), end.astimezone(UTC)


def eastern_day(moment: datetime) -> date:
    return _aware(moment).astimezone(EASTERN).date()


def rollup_day(session: Session, term_code: str, day: date, *, now: datetime,
               windows=None) -> DayRollup:
    """Compute (or recompute) one day's snapshot of every course of the term.
    The caller owns the transaction."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                        {"key": f"course_demand_rollup:{term_code}"})
    start, end = day_bounds(day)
    window = first_window(term_code, windows)
    opens_at = window.opens_at.astimezone(UTC) if window else None

    refresh_times = sorted(t for t in (_aware(v) for v in session.scalars(
        select(AvailabilityRefresh.observed_at).where(
            AvailabilityRefresh.term_code == term_code,
            AvailabilityRefresh.verdict == "accepted"))) if t < end)
    today = [t for t in refresh_times if t >= start]
    gap = None
    if today:
        edges = [start, *today, end]
        gap = int(max(b - a for a, b in zip(edges, edges[1:], strict=False)).total_seconds())

    # Each index's history up to the end of the day, in observation order.
    status_at_end: dict[str, str] = {}
    status_at_open: dict[str, str] = {}
    first_closed_change: dict[str, tuple[datetime, str | None]] = {}
    for change in session.scalars(
            select(SectionAvailabilityChange)
            .where(SectionAvailabilityChange.term_code == term_code)
            .order_by(SectionAvailabilityChange.observed_at, SectionAvailabilityChange.id)):
        at = _aware(change.observed_at)
        if at >= end:
            continue
        status_at_end[change.index_number] = change.to_status
        if opens_at is None:
            continue
        if at < opens_at:
            status_at_open[change.index_number] = change.to_status
        elif change.to_status == "closed" and change.index_number not in first_closed_change:
            first_closed_change[change.index_number] = (at, change.from_status)

    courses: dict = defaultdict(list)
    names: dict = {}
    for index, course_id, course_string in session.execute(
            select(CourseSection.index_number, Course.id, Course.course_string)
            .join(CourseOffering, CourseOffering.id == CourseSection.offering_id)
            .join(Course, Course.id == CourseOffering.course_id)
            .where(CourseSection.term_code == term_code)):
        courses[course_id].append(index)
        names[course_id] = course_string

    existing = {row.course_id: row for row in session.scalars(
        select(CourseDemandSnapshot).where(CourseDemandSnapshot.term_code == term_code,
                                           CourseDemandSnapshot.snapshot_date == day))}
    report = DayRollup(term_code, day)
    window_started = opens_at is not None and opens_at < end
    for course_id, indexes in courses.items():
        states = [status_at_end.get(i) for i in indexes]
        open_count = states.count("open")
        closed_count = states.count("closed")
        observed = open_count + closed_count
        reason = ("no_accepted_refresh_that_day" if not today
                  else "no_section_observed" if observed == 0 else None)
        first_closed = {}
        if window_started and reason is None:
            for i in sorted(indexes):
                entry = _first_closed(i, first_closed_change, status_at_open, refresh_times)
                if entry is not None:
                    first_closed[i] = entry
        values = dict(
            course_string=names[course_id], section_count=len(indexes),
            status="ok" if reason is None else "insufficient_data",
            insufficient_reason=reason,
            observed_count=None if reason else observed,
            open_count=None if reason else open_count,
            closed_count=None if reason else closed_count,
            closed_share=None if reason else (
                Decimal(closed_count) / Decimal(observed)).quantize(Decimal("0.0001")),
            first_closed=first_closed, window_opens_at=opens_at,
            refreshes=len(today), max_refresh_gap_seconds=gap, computed_at=now)
        row = existing.get(course_id)
        if row is None:
            session.add(CourseDemandSnapshot(term_code=term_code, course_id=course_id,
                                             snapshot_date=day, **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
        if reason is None:
            report.ok += 1
        else:
            report.insufficient += 1
            report.reasons[reason] = report.reasons.get(reason, 0) + 1
    session.flush()
    return report


def _first_closed(index, first_closed_change, status_at_open, refresh_times):
    hit = first_closed_change.get(index)
    if hit is not None:
        at, from_status = hit
        if from_status is None:                     # first ever seen, already closed
            return {"kind": "closed_when_first_observed", "observed_at": None,
                    "not_before": None}
        before = bisect.bisect_left(refresh_times, at)
        not_before = refresh_times[before - 1] if before else None
        return {"kind": "closed_after_window", "observed_at": at.isoformat(),
                "not_before": not_before.isoformat() if not_before else None}
    if status_at_open.get(index) == "closed":
        return {"kind": "closed_before_window", "observed_at": None, "not_before": None}
    return None                                     # not closed since the window opened


def due_days(session: Session, term_code: str, *, now: datetime,
             lookback_days: int = LOOKBACK_DAYS) -> list[date]:
    """Completed Eastern days, from the term's first accepted refresh (at most
    `lookback_days` ago), that have no snapshot row yet."""
    firsts = [_aware(t) for t in session.scalars(
        select(AvailabilityRefresh.observed_at).where(
            AvailabilityRefresh.term_code == term_code,
            AvailabilityRefresh.verdict == "accepted"))]
    if not firsts:
        return []
    yesterday = eastern_day(now) - timedelta(days=1)
    first = max(eastern_day(min(firsts)), yesterday - timedelta(days=lookback_days - 1))
    done = set(session.scalars(
        select(CourseDemandSnapshot.snapshot_date).distinct()
        .where(CourseDemandSnapshot.term_code == term_code)))
    days, day = [], first
    while day <= yesterday:
        if day not in done:
            days.append(day)
        day += timedelta(days=1)
    return days


def rollup_due(session: Session, *, now: datetime, windows=None) -> list[DayRollup]:
    """Roll up every due day of every ACTIVE polled term; commits per day."""
    out = []
    terms = session.scalars(select(AvailabilityPollSchedule.term_code)
                            .where(AvailabilityPollSchedule.active.is_(True))
                            .order_by(AvailabilityPollSchedule.term_code)).all()
    for term_code in terms:
        for day in due_days(session, term_code, now=now):
            out.append(rollup_day(session, term_code, day, now=now, windows=windows))
            session.commit()
    return out


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; every stored time is UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


__all__ = ["LOOKBACK_DAYS", "DayRollup", "day_bounds", "due_days", "eastern_day",
           "rollup_day", "rollup_due"]
