"""Rutgers term codes for planning (Phase 6.5).

`YYYYT`, T = 0 winter session (Dec/Jan, before spring of year YYYY), 1 spring,
7 summer, 9 fall - the codes SOC itself uses. They sort chronologically as
integers (app.domain.attempts.term_order).
"""

from __future__ import annotations

from app.domain.attempts import term_order

SEASON_NAMES = {"0": "Winter", "1": "Spring", "7": "Summer", "9": "Fall"}
_ORDER = ("0", "1", "7", "9")


def is_term(code: str | None) -> bool:
    return term_order(code) is not None


def season(code: str) -> str:
    return code[4]


def label(code: str) -> str:
    return f"{SEASON_NAMES[season(code)]} {code[:4]}"


def next_term(code: str) -> str:
    year, s = int(code[:4]), season(code)
    i = _ORDER.index(s)
    return f"{year}{_ORDER[i + 1]}" if i + 1 < len(_ORDER) else f"{year + 1}{_ORDER[0]}"


def planning_terms(start: str, count: int, *, include_summer: bool,
                   include_winter: bool) -> list[str]:
    """`count` planning terms from `start` (inclusive), skipping excluded seasons."""
    allowed = ({"1", "9"} | ({"7"} if include_summer else set())
               | ({"0"} if include_winter else set()))
    out: list[str] = []
    code = start
    while len(out) < count:
        if season(code) in allowed:
            out.append(code)
        code = next_term(code)
    return out


__all__ = ["SEASON_NAMES", "is_term", "label", "next_term", "planning_terms", "season"]
