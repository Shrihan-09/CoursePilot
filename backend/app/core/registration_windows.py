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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
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
                  windows=None) -> timedelta:
    """90 s inside the term's registration window; otherwise 30 minutes (or the
    schedule's own interval, if an operator set it slower)."""
    if window_at(term_code, moment, windows) is not None:
        return timedelta(seconds=WINDOW_INTERVAL_SECONDS)
    return timedelta(seconds=max(base_seconds, OUTSIDE_INTERVAL_SECONDS))


__all__ = ["EASTERN", "OUTSIDE_INTERVAL_SECONDS", "REGISTRATION_WINDOWS", "RegistrationWindow",
           "WINDOW_INTERVAL_SECONDS", "first_window", "poll_interval", "window_at"]
