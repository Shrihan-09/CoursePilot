"""Rutgers registration windows - when openSections is polled faster (Phase 7.3).

Demand data (how fast sections fill) is only measurable while students are
registering, so the live worker polls a term every WINDOW_INTERVAL inside its
registration window and every OUTSIDE_INTERVAL otherwise - one openSections
request per term per poll either way (app.services.live.next_due).

Every window is copied from a published Rutgers source, cited per entry;
nothing is guessed. Times are America/New_York.

* Spring 2027 (20271): "Registration for the Spring 2027 semester begins on
  Monday, November 9th at 9:00 am and continues through Thursday, November
  19th" - Office of the Registrar, New Brunswick, "Spring 2027 Registration
  Schedule", https://nbregistrar.rutgers.edu/undergrad/S27prereg.htm (page
  "Updated: October 15, 2026"; read 2026-10-24). WebReg hours run to 2:00 am,
  so the window closes at 2:00 am on Friday, November 20. Registration then
  continues through the add/drop period, whose dates were not published in a
  source CoursePilot could read; that window is NOT configured (Spring 2027
  classes begin Tuesday, January 19, 2027 - https://scheduling.rutgers.edu/academic-calendar/).

Registration SCHEDULES (degree-credit tiers, the nightly WebReg closure) are
data files in app/core/data/, each with its source note. Spring 2027
(registration_schedule_20271.json) is copied from an owner-provided screenshot
of https://nbregistrar.rutgers.edu/undergrad/s27prereg.htm (recorded
2026-11-08): tiers 105/90/75/60/40/25/001/0 degree credits open at 9:00 am on
Nov 9/10/11/12/13/16/17/18, all students Nov 19 at 9:00 am, and WebReg is
closed nightly 2:00-6:00 am. During the closure the window cadence drops to
OUTSIDE_INTERVAL; `registration_phase` tags any timestamp with the tier that
was registering then.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

#: Inside a registration window: Rutgers' openSections is cached for 30 s
#: (max-age=30); 90 s is the existing per-term cadence.
WINDOW_INTERVAL_SECONDS = 90
#: Outside every window.
OUTSIDE_INTERVAL_SECONDS = 1800


@dataclass(frozen=True, slots=True)
class RegistrationWindow:
    term_code: str
    opens_at: datetime
    closes_at: datetime
    label: str
    source_url: str

    def contains(self, moment: datetime) -> bool:
        return self.opens_at <= moment < self.closes_at


REGISTRATION_WINDOWS: tuple[RegistrationWindow, ...] = (
    RegistrationWindow(
        term_code="20271",
        opens_at=datetime(2026, 11, 9, 9, 0, tzinfo=EASTERN),
        closes_at=datetime(2026, 11, 20, 2, 0, tzinfo=EASTERN),
        label="Spring 2027 registration (Nov 9, 9:00 am - Nov 19, WebReg until 2:00 am)",
        source_url="https://nbregistrar.rutgers.edu/undergrad/S27prereg.htm",
    ),
)


def _configured(windows):
    # Read at CALL time, so the list is one place to change (and to test).
    return REGISTRATION_WINDOWS if windows is None else windows


def window_at(term_code: str, moment: datetime, windows=None) -> RegistrationWindow | None:
    return next((w for w in _configured(windows)
                 if w.term_code == term_code and w.contains(moment)), None)


def first_window(term_code: str, windows=None) -> RegistrationWindow | None:
    """The term's earliest configured window (when registration first opens)."""
    own = sorted((w for w in _configured(windows) if w.term_code == term_code),
                 key=lambda w: w.opens_at)
    return own[0] if own else None


def poll_interval(term_code: str, base_seconds: int, moment: datetime,
                  windows=None, schedules=None) -> timedelta:
    """90 s inside the term's registration window; otherwise 30 minutes (or the
    schedule's own interval, if an operator set it slower). During WebReg's
    nightly closure (2:00-6:00 am Eastern) nobody can add or drop, so the
    window cadence drops back to 30 minutes."""
    if window_at(term_code, moment, windows) is not None \
            and not in_nightly_closure(term_code, moment, schedules):
        return timedelta(seconds=WINDOW_INTERVAL_SECONDS)
    return timedelta(seconds=max(base_seconds, OUTSIDE_INTERVAL_SECONDS))


# --------------------------------------------------------------------------
# Registration schedules: degree-credit tiers and the nightly WebReg closure
# --------------------------------------------------------------------------
#
# One JSON file per term in app/core/data/registration_schedule_<term>.json,
# each with its source note (the Registrar page it was copied from). Times are
# America/New_York wall-clock times.

_DATA = Path(__file__).with_name("data")


@dataclass(frozen=True, slots=True)
class CreditTier:
    min_degree_credits: int
    label: str
    opens_at: datetime


@dataclass(frozen=True, slots=True)
class RegistrationSchedule:
    term_code: str
    term_label: str
    tiers: tuple[CreditTier, ...]          # earliest first (highest credits first)
    all_students_opens_at: datetime
    closure_start: time
    closure_end: time
    source_url: str
    source_basis: str

    def tier_for_credits(self, degree_credits: float | Decimal) -> CreditTier:
        """The tier a student with this many DEGREE credits registers in."""
        return next(t for t in self.tiers if degree_credits >= t.min_degree_credits)


@dataclass(frozen=True, slots=True)
class RegistrationPhase:
    """Who could register at a moment: `phase` is before_registration |
    tier | all_students | nightly_closure."""

    phase: str
    tier: CreditTier | None = None

    @property
    def label(self) -> str:
        if self.phase == "tier" and self.tier is not None:
            return self.tier.label
        return self.phase.replace("_", " ")


def _load(path: Path) -> RegistrationSchedule:
    raw = json.loads(path.read_text(encoding="utf-8"))

    def at(text: str) -> datetime:
        return datetime.fromisoformat(text).replace(tzinfo=EASTERN)

    tiers = tuple(sorted((CreditTier(t["min_degree_credits"], t["label"], at(t["opens_at"]))
                          for t in raw["tiers"]), key=lambda t: t.opens_at))
    closure = raw["nightly_closure"]
    return RegistrationSchedule(
        term_code=raw["term_code"], term_label=raw["term_label"], tiers=tiers,
        all_students_opens_at=at(raw["all_students_opens_at"]),
        closure_start=time.fromisoformat(closure["start"]),
        closure_end=time.fromisoformat(closure["end"]),
        source_url=raw["source"]["url"], source_basis=raw["source"]["basis"])


@lru_cache(maxsize=1)
def _bundled() -> dict[str, RegistrationSchedule]:
    out = {}
    for path in sorted(_DATA.glob("registration_schedule_*.json")):
        schedule = _load(path)
        out[schedule.term_code] = schedule
    return out


def registration_schedule(term_code: str, schedules=None) -> RegistrationSchedule | None:
    return (_bundled() if schedules is None else schedules).get(term_code)


def in_nightly_closure(term_code: str, moment: datetime, schedules=None) -> bool:
    schedule = registration_schedule(term_code, schedules)
    if schedule is None:
        return False
    local = moment.astimezone(EASTERN).time()
    return schedule.closure_start <= local < schedule.closure_end


def registration_phase(term_code: str, moment: datetime,
                       schedules=None) -> RegistrationPhase | None:
    """Which registration tier was active at `moment` (None: no schedule on
    file for the term). Computable after the fact from any timestamp, so a
    recorded status change is tagged without storing anything new."""
    schedule = registration_schedule(term_code, schedules)
    if schedule is None:
        return None
    if in_nightly_closure(term_code, moment, schedules):
        return RegistrationPhase("nightly_closure")
    if moment >= schedule.all_students_opens_at:
        return RegistrationPhase("all_students")
    opened = [t for t in schedule.tiers if t.opens_at <= moment]
    if not opened:
        return RegistrationPhase("before_registration")
    return RegistrationPhase("tier", opened[-1])


__all__ = ["CreditTier", "EASTERN", "OUTSIDE_INTERVAL_SECONDS", "REGISTRATION_WINDOWS",
           "RegistrationPhase", "RegistrationSchedule", "RegistrationWindow",
           "WINDOW_INTERVAL_SECONDS", "first_window", "in_nightly_closure", "poll_interval",
           "registration_phase", "registration_schedule", "window_at"]
