"""Co-requisite parsing (Phase 6.4) - course-taking eligibility, not degree completion.

A co-requisite is a TERM fact: "may/must be taken in the same term". It is
evaluated against completed courses PLUS a proposed term's courses
(app.services.course_eligibility); nothing here proposes a term.

## Observed SOC forms (docs/investigations/evidence/phase-6-4-rule-inventory.json)

| form (verbatim examples) | kind | completed earlier | same term |
|---|---|---|---|
| "PRE OR COREQ: 01:146:356", "PRE/CO-REQ: 01:146:328", "PREREQ OR COREQ - 940:203 OR PERM. OF DEPT." | `pre_or_co` | satisfies | satisfies |
| "CO-REQ: 119:116", "COREQ: 640:111 OR 115" | `co` | UNKNOWN - Rutgers does not say whether earlier completion suffices | satisfies |
| "THIS COURSE MUST BE TAKEN CONCURRENTLY WITH ONE OF THE FOLLOWING COURSES: 07:700:210, 381, 382, OR 384" | `concurrent_only` | does not satisfy ("must be taken concurrently") | satisfies |

Course lists use OR / commas; a bare number inherits the subject of the code
before it ("640:111 OR 115"). "OR PERM. OF DEPT." / "INSTRUCTOR PERMISSION"
become an alternative CoursePilot cannot check (UNKNOWN). Not interpreted, and
kept as `unsupported`: "OR HIGHER" ("COREQ: 01:640:112 OR HIGHER"), "COURSE
FROM 198, 615, ...", AND-lists, wrap-damaged codes ("01:37 7:370"),
"RECOMMENDED AS A COREQUISITE" (advice, not a rule), "MAY TAKE ...
CONCURRENTLY" (permission, not a rule).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.conditions import COREQ_MARKER, Resolve, clean, course_codes
from app.domain.prerequisites import (
    AnyOf,
    ConcurrentReq,
    Expr,
    Unsupported,
    canonicalize,
    to_text,
)

COREQUISITE_PARSER_VERSION = "1"

_MARKER = COREQ_MARKER
_ADVICE_BEFORE = re.compile(r"RECOMMENDED(\s+AS)?(\s+AN?)?\s*$", re.I)
_STOP = re.compile(r"\b(PRE-?\s?REQ(UISITE)?S?|LABS?|CHE SCHEDULE|THIS APPLIES|APPLIES TO|"
                   r"STUDENTS|OPEN ONLY|NOT OPEN|MAJORS|IF CLOSED|SPN|SPECIAL PERMISSION)\b", re.I)
_PERMISSION = re.compile(r"^(PERM|PERMISSION)( OF( THE)? (DEPT|DEPARTMENT))?$|"
                         r"^(INSTRUCTOR'?S? )?PERMISSION( OF( THE)? INSTRUCTOR)?$", re.I)
_CODE_ITEM = re.compile(r"^(\d{2}:)?\d{3}:\d{3}$|^\d{3}$")


@dataclass(frozen=True, slots=True)
class CoreqParse:
    kind: str                     # pre_or_co | co | concurrent_only | none
    classification: str           # parsed | unsupported
    expression: Expr | None
    courses: tuple[str, ...]
    detail: str | None = None

    @property
    def canonical_text(self) -> str | None:
        return None if self.expression is None else to_text(self.expression)


def find_clauses(text: str | None) -> list[tuple[str, str]]:
    """(kind, body) for every co-requisite marker in a note."""
    t = clean(text)
    if not t or not _MARKER.search(t):
        return []
    out = []
    matches = list(_MARKER.finditer(t))
    for i, m in enumerate(matches):
        if _ADVICE_BEFORE.search(t[:m.start()]):
            continue                    # "RECOMMENDED AS A COREQUISITE" - advice, not a rule
        kind = next(k for k in ("pre_or_co", "co", "concurrent_only") if m.group(k))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(t)
        body = t[m.end():end]
        body = re.split(r"[;.]", body, maxsplit=1)[0]
        stop = _STOP.search(body)
        if stop:
            body = body[:stop.start()]
        out.append((kind, body.strip(" :-,")))
    return out


def parse_clause(kind: str, body: str, resolve: Resolve) -> CoreqParse:
    prior = {"pre_or_co": True, "co": None, "concurrent_only": False}[kind]
    if not body:
        return CoreqParse(kind, "unsupported", None, (), "empty course list")
    if re.search(r"\bOR HIGHER\b|\bAND\b|&|\bCOURSE FROM\b|\bEQUIV", body, re.I):
        return CoreqParse(kind, "unsupported", None, (), f"not interpreted: {body[:80]}")
    items = [i.strip(" ,") for i in re.split(r"\bOR\b|,", body, flags=re.I) if i.strip(" ,")]
    alternatives: list[Expr] = []
    for item in items:
        if _PERMISSION.match(item):
            alternatives.append(Unsupported("permission", item))
        elif not _CODE_ITEM.match(item):
            return CoreqParse(kind, "unsupported", None, (), f"unrecognized item: {item[:60]}")
    codes, complete = course_codes(body, resolve)
    if not codes or not complete:
        return CoreqParse(kind, "unsupported", None, tuple(codes), "unresolved course code")
    expr = canonicalize(AnyOf(tuple([ConcurrentReq(c, prior) for c in codes] + alternatives)))
    return CoreqParse(kind, "parsed", expr, tuple(sorted(codes)))


def parse_note(text: str | None, resolve: Resolve) -> list[CoreqParse]:
    return [parse_clause(kind, body, resolve) for kind, body in find_clauses(text)]


__all__ = ["COREQUISITE_PARSER_VERSION", "CoreqParse", "find_clauses", "parse_clause",
           "parse_note"]
