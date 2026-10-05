"""Meeting normalization and conflict semantics (Phase 6.6) - pure functions.

## What a SOC meeting row can be (measured on five archived NB terms)

| row | kind | conflicts? |
|---|---|---|
| weekday + start/end military time, end > start | `timed` | interval overlap (below) |
| no day/time, mode "ONLINE INSTRUCTION(INTERNET)" | `asynchronous` | never - online by design |
| no day/time, class mode (LEC, RECIT, LAB, SEM ...) | `tba` | UNKNOWN |
| no day/time, any other mode (RSCH-MA, PROJ-IND, INTERNSP, MUS-INDV ...) | `arranged` | UNKNOWN |
| a time that does not parse, or end <= start | `malformed` | UNKNOWN |

Only `asynchronous` is free time. "No meeting time" is otherwise a fact
CoursePilot does not know, and a schedule containing it is returned only
with `time_verified = false` and ranked below verified schedules.
Malformed rows are real: 07:966:333 publishes T/F 2330-1250 (12-hour
1130-1250 in the same record) and 07:966:123 publishes S 1100-1100.

## Conflict

Two timed meetings conflict when they are on the same weekday, their active
DATE ranges may overlap, and

    start_A < end_B + buffer   and   start_B < end_A + buffer

With buffer 0 (the default) back-to-back classes (end_A == start_B) do not
conflict. `buffer` is the student's own `minimum_minutes_between_classes`;
CoursePilot assumes no travel time of its own.

## Dates

SOC publishes `sessionDates` for Summer sections ("05/26/2026 - 07/02/2026")
and for none of the Fall/Spring sections in the archive. Two meetings whose
date ranges are both known and disjoint never conflict. When either range is
unknown the ranges are assumed to overlap: an unknown date can only ADD a
conflict, never hide one. No date is ever invented.
"""

from __future__ import annotations

import re
from datetime import date

from app.domain.schedule import TIME_UNKNOWN_KINDS, WEEKDAYS, MeetingInterval, MeetingKind

ONLINE_MODE_DESC = "ONLINE INSTRUCTION(INTERNET)"
#: Class meeting modes: without a time these are TBA, not "by arrangement".
CLASS_MODES = frozenset({"LEC", "RECIT", "LAB", "SEM", "STUDIO", "WORKSHOP", "LEC/LAB",
                         "LEC/RECIT", "PRACT", "DISC"})

_MILITARY = re.compile(r"^([01][0-9]|2[0-3])([0-5][0-9])$")


def minutes(military: str | None) -> int | None:
    """'1550' -> 950. None for anything that is not a valid HHMM time."""
    if not military:
        return None
    m = _MILITARY.match(military.strip())
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def normalize(*, day: str | None, start: str | None, end: str | None,
              mode_code: str | None, mode_desc: str | None,
              start_date: date | None = None, end_date: date | None = None,
              campus: str | None = None, campus_name: str | None = None,
              building: str | None = None, room: str | None = None) -> MeetingInterval:
    """One stored SOC meeting row -> a MeetingInterval (raw values kept)."""
    raw = {"meeting_day": day, "start_time_military": start, "end_time_military": end}
    common = dict(mode_code=mode_code, mode_desc=mode_desc, campus=campus,
                  campus_name=campus_name, building=building, room=room,
                  start_date=start_date, end_date=end_date, raw=raw)
    if not day and not start and not end:
        desc = (mode_desc or "").strip().upper()
        if desc == ONLINE_MODE_DESC:
            kind = MeetingKind.ASYNCHRONOUS
        elif desc in CLASS_MODES:
            kind = MeetingKind.TBA
        else:
            kind = MeetingKind.ARRANGED
        return MeetingInterval(kind=kind, **common)
    s, e = minutes(start), minutes(end)
    if day not in WEEKDAYS or s is None or e is None or e <= s:
        return MeetingInterval(kind=MeetingKind.MALFORMED, day=day, **common)
    return MeetingInterval(kind=MeetingKind.TIMED, day=day, start_minute=s, end_minute=e,
                           **common)


def dates_may_overlap(a: MeetingInterval, b: MeetingInterval) -> bool:
    if None in (a.start_date, a.end_date, b.start_date, b.end_date):
        return True
    return a.start_date <= b.end_date and b.start_date <= a.end_date


def conflict(a: MeetingInterval, b: MeetingInterval, buffer: int = 0) -> bool:
    """A definite conflict between two meetings (only timed meetings can have one)."""
    if a.kind is not MeetingKind.TIMED or b.kind is not MeetingKind.TIMED:
        return False
    if a.day != b.day or not dates_may_overlap(a, b):
        return False
    return a.start_minute < b.end_minute + buffer and b.start_minute < a.end_minute + buffer


def sections_conflict(a: list[MeetingInterval], b: list[MeetingInterval],
                      buffer: int = 0) -> tuple[MeetingInterval, MeetingInterval] | None:
    """The first conflicting pair over EVERY meeting of both sections, or None."""
    for x in a:
        for y in b:
            if conflict(x, y, buffer):
                return x, y
    return None


def time_verified(meetings: list[MeetingInterval]) -> bool:
    return all(m.kind not in TIME_UNKNOWN_KINDS for m in meetings)


def hhmm(value: str | None) -> int | None:
    """'10:00' -> 600."""
    if not value:
        return None
    h, m = value.split(":")
    return int(h) * 60 + int(m)


def violates_window(meetings: list[MeetingInterval], *, earliest: int | None,
                    latest: int | None, avoid_days: list[str]) -> str | None:
    """The hard time-window constraint a section breaks, if any."""
    for m in meetings:
        if m.kind is not MeetingKind.TIMED:
            continue
        if m.day in avoid_days:
            return "avoid_days"
        if earliest is not None and m.start_minute < earliest:
            return "earliest_start"
        if latest is not None and m.end_minute > latest:
            return "latest_end"
    return None


__all__ = ["CLASS_MODES", "ONLINE_MODE_DESC", "conflict", "dates_may_overlap", "hhmm",
           "minutes", "normalize", "sections_conflict",
           "time_verified", "violates_window"]
