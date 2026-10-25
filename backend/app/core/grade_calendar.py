"""When each term's FINAL grades are in (Phase 7.4: Degree Navigator audit freshness).

A DN audit printed before the most recent term's final grades shows that
term's courses as current, so it is labelled stale (decision 6 of the 7.4
answers). Every date is copied from a published Rutgers New Brunswick
Registrar calendar, cited per entry; a term without a published date is not
listed and the rule is then simply not applied for it - never estimated.
For a grading PERIOD the date is its last day.

* Fall 2025 (20259) - "Fall grades due - Fall TGR process to update credits
  and GPA", January 2, 2026; Winter 2026 (20260) - "Winter Session grades
  due", January 23, 2026; Spring 2026 (20261) - "Spring grading period",
  May 7 - 15, 2026: https://nbregistrar.rutgers.edu/undergrad/s26ugcal.htm
  (page updated March 23, 2026; read 2026-10-24).
* Summer 2026 (20267) - "Summer grades due - Summer TGR process to update
  credits and GPA", August 14, 2026; Fall 2026 (20269) - "Fall grading
  period", December 15, 2026 - January 4, 2027:
  https://nbregistrar.rutgers.edu/undergrad/f26ugcal.htm (page updated
  August 7, 2026; read 2026-10-24).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

FINAL_GRADES = {
    "20259": (date(2026, 1, 2), "https://nbregistrar.rutgers.edu/undergrad/s26ugcal.htm"),
    "20260": (date(2026, 1, 23), "https://nbregistrar.rutgers.edu/undergrad/s26ugcal.htm"),
    "20261": (date(2026, 5, 15), "https://nbregistrar.rutgers.edu/undergrad/s26ugcal.htm"),
    "20267": (date(2026, 8, 14), "https://nbregistrar.rutgers.edu/undergrad/f26ugcal.htm"),
    "20269": (date(2027, 1, 4), "https://nbregistrar.rutgers.edu/undergrad/f26ugcal.htm"),
}


def final_grades_at(term_code: str) -> datetime | None:
    """The end of the published final-grades day (Eastern), or None."""
    entry = FINAL_GRADES.get(term_code)
    if entry is None:
        return None
    return datetime.combine(entry[0] + timedelta(days=1), time(0), tzinfo=EASTERN)


def latest_final_grades(now: datetime) -> tuple[str, datetime] | None:
    """The most recent term whose final grades are in by `now`."""
    passed = [(t, final_grades_at(t)) for t in FINAL_GRADES]
    passed = [(t, at) for t, at in passed if at is not None and at <= now]
    return max(passed, key=lambda p: p[1]) if passed else None


__all__ = ["FINAL_GRADES", "final_grades_at", "latest_final_grades"]
