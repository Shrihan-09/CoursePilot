"""Where "now" is in the Rutgers year - for choosing a plan's START only (Phase 7.2.1).

Phase 7.2 started a plan at the term after the student's latest recorded term
and deliberately read no clock. The persona audit showed why that is not
enough: a student whose record ends in Fall 2021, or one who simply has not
entered this semester's courses, was offered a start in the PAST, and a
returning student got an empty plan for 2022.

The policy (DATA_MODEL section 49):

    default start = the later of
        the first regular (Fall/Spring) term after the CURRENT term, and
        the first regular term after the latest COMPLETED or IN-PROGRESS term

Planned rows do not move the floor: a course the student merely intends to
take is not history (and the planner does not consume planned rows).

The term boundaries below are APPROXIMATE (Rutgers publishes exact dates per
year). They decide only which future term a plan starts in by default - never
eligibility, never a requirement, never a schedule. `academic_today` (settings)
pins the date for tests and QA.
"""

from __future__ import annotations

from datetime import date

from app.core.config import get_settings
from app.services.planning.terms import next_term

#: (month, day) each season starts, in calendar order. Winter session spans
#: the new year: late December belongs to the NEXT year's winter term.
_SPRING_START = (1, 20)
_SUMMER_START = (5, 16)
_FALL_START = (9, 1)
_WINTER_START = (12, 23)


def today() -> date:
    return get_settings().academic_today or date.today()


def current_term(on: date | None = None) -> str:
    """The Rutgers term in session (or between sessions, the one just ended)."""
    d = on or today()
    md = (d.month, d.day)
    if md >= _WINTER_START:
        return f"{d.year + 1}0"
    if md >= _FALL_START:
        return f"{d.year}9"
    if md >= _SUMMER_START:
        return f"{d.year}7"
    if md >= _SPRING_START:
        return f"{d.year}1"
    return f"{d.year}0"


def next_regular_after(term: str) -> str:
    code = next_term(term)
    while code[4] not in "19":
        code = next_term(code)
    return code


def default_start(latest_history_term: str | None, on: date | None = None) -> str:
    """The first future Fall/Spring term that also follows the record."""
    floor = next_regular_after(current_term(on))
    if latest_history_term is not None:
        after_record = next_regular_after(latest_history_term)
        if after_record > floor:
            floor = after_record
    return floor


def start_options(first: str, count: int, *, include_summer: bool,
                  include_winter: bool) -> list[str]:
    """`count` start terms from `first`, in the seasons the student plans in."""
    allowed = {"1", "9"} | ({"7"} if include_summer else set()) | ({"0"} if include_winter else set())
    out: list[str] = []
    code = first
    while len(out) < count:
        if code[4] in allowed:
            out.append(code)
        code = next_term(code)
    return out


__all__ = ["current_term", "default_start", "next_regular_after", "start_options", "today"]
