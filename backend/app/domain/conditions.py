"""Reading SOC prerequisite CONDITION notes (Phase 6.4) - course eligibility only.

Phase 6.2 stored `courseNotes` that state a prerequisite condition and never
interpreted them: any such note capped SATISFIED at UNKNOWN. Phase 6.4
interprets the patterns observed in the archived SOC (five terms; see
docs/investigations/evidence/phase-6-4-rule-inventory.json) and NOTHING else.

## Patterns interpreted

Minimum grade - three scopes, because Rutgers words it three ways:

| text (verbatim examples) | scope |
|---|---|
| "A grade below a 'C' in a prerequisite course will not satisfy prereq" (CS, 40 offerings) | `all` |
| "Student needs C or better in all prerequisites." (Chemistry, 13) | `all` |
| "Prerequisites are grades of C or higher in Intro to Micro 220:102, Intro to Macro 220:103, and Calculus I 640:135 or 151" (Economics) | `named` |
| "PRE-REQ 01:447:384 WITH A GRADE OF - C - OR BETTER" | `named` |
| "C OR BETTER NEEDED IN MATH 300" (names, no codes) | `unspecified` |

`named` applies strictly to the courses the note names by code; the
prerequisite's OTHER courses (equivalents the note does not mention) are
ambiguous. `unspecified` makes every course ambiguous. Ambiguous means: a
grade at or above the minimum satisfies under any reading; a passing grade
below it is UNKNOWN - never guessed either way.

Alternatives - "FOR ALL SECTIONS: PREREQ - 940:102 OR 121 OR PLACEMENT TEST"
restates the prerequisite and ADDS an alternative CoursePilot cannot check.
Interpreted only when the courses it restates equal the prerequisite's own
courses. Evaluation is then (prerequisite OR unknown): SATISFIED stays
SATISFIED, and an unmet prerequisite becomes UNKNOWN - before 6.4 it was
UNSATISFIED, which was wrong for a student placed by examination.

## Everything else

Permission-only, program restrictions, GPA conditions, co-requisites (read by
app.domain.corequisites), advisory prose: classified, kept, never evaluated -
they keep capping SATISFIED at UNKNOWN exactly as in Phase 6.2.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable

from app.domain.prerequisites import _CONDITION_KINDS

CONDITION_PARSER_VERSION = "2"

Resolve = Callable[[str, str], str | None]

_TAG = re.compile(r"<[^>]+>")
_ABBREV = [(re.compile(r"\bPERM\.", re.I), "PERM"), (re.compile(r"\bDEPT\.", re.I), "DEPT"),
           (re.compile(r"\bW/O\b", re.I), "WITHOUT")]
_SPLIT = re.compile(r"(?<=[.;])\s+|;\s*")
_GRADE = r"(?P<g>[A-D]\+?)"

_ALL_SCOPE = [
    re.compile(rf"grade below an? '?{_GRADE}'? in a prerequisite course will not satisfy", re.I),
    re.compile(rf"needs? {_GRADE} or (better|higher) in all prerequisites", re.I),
]
_NAMED_SCOPE = [
    re.compile(rf"prerequisites? (are|is) (minimum )?grades? of {_GRADE}( or (better|higher))? "
               rf"(in|for) (?P<list>.+)", re.I),
    re.compile(rf"(?P<list>.+?) with (a )?grade of\s*-?\s*{_GRADE}\s*-?\s*or (better|higher)", re.I),
    re.compile(rf"(?P<list>.+?) with (a )?{_GRADE} or (better|higher)", re.I),
    re.compile(rf"(?P<list>.+?) with grade {_GRADE} or (better|higher)", re.I),
    re.compile(rf"{_GRADE} or (better|higher) (needed|required) in (?P<list>.+)", re.I),
]
_ADVISORY = re.compile(r"\b(recommend|encourag|suggest|strongly advised|should)\w*", re.I)
_SIGNAL = re.compile(r"pre-?req|prerequisite|co-?req|corequisite|concurrent|placement|permission|"
                     r"\bperm\b|\bgrade|\bG ?PA\b|majors?\b|minors?\b|declared|restricted|open only|"
                     r"not open", re.I)
_RESTATEMENT = re.compile(r"^(FOR ALL SECTIONS:\s*)?PRE-?REQ(UISITE)?S?\s*[-:]?\s*(?P<body>.+?)\.?$",
                          re.I)
#: Registration logistics that may trail a restated prerequisite. A WHITELIST:
#: anything else after the course list ("AND APPROVAL BY ... COMMITTEE") is
#: a condition and is never dropped.
#: Phase 6.6.1: "MUST REGISTER [FOR] BOTH LEC/REC & LAB" (01:750:194) is the
#: same course's REGISTRATION bundle - enforced by the Schedule Engine
#: (app.services.scheduling.candidates), not an academic condition.
_LOGISTICS = re.compile(r"^(IF CLOSED\b|LABS? BEGIN\b|(THIS )?APPLIES TO ALL\b|"
                        r"(FOR )?ALL SECTIONS\b|MUST REGISTER (FOR )?BOTH\b)", re.I)
#: Where a co-requisite clause starts. Co-requisites are read by
#: app.domain.corequisites; this module leaves them alone.
COREQ_MARKER = re.compile(
    r"(?P<pre_or_co>\b(?:ADDITIONAL\s+)?PRE(?:-?\s?REQ(?:UISITE)?S?)?-?\s*(?:OR|/)\s*"
    r"CO-?\s?REQ(?:UISITE)?S?\b)"
    r"|(?P<co>\bCO-?\s?REQ(?:UISITE)?S?\b)"
    r"|(?P<concurrent_only>\bMUST BE TAKEN CONCURRENTLY WITH(?: ONE OF)?(?: THE FOLLOWING"
    r"(?: COURSES)?)?\b)", re.I)
_ALT_KINDS = [("placement", re.compile(r"^PLACEMENT( TEST)?$", re.I)),
              ("equivalent", re.compile(r"^(AN? )?(EQUIV|EQUIVALENT|EQIV|EQUVALENT)S?\.?$", re.I)),
              ("permission", re.compile(r"^(PERM|PERMISSION)( OF( THE)? (DEPT|DEPARTMENT))?$|"
                                        r"^(INSTRUCTOR'?S? )?PERMISSION( OF( THE)? INSTRUCTOR)?$",
                                        re.I))]


def clean(text: str | None) -> str:
    text = html.unescape(_TAG.sub(" ", text or ""))
    for pattern, repl in _ABBREV:
        text = pattern.sub(repl, text)
    return re.sub(r"\s+", " ", text).strip()


def clauses(text: str) -> list[str]:
    return [c.strip(" .") for c in _SPLIT.split(clean(text)) if c.strip(" .")]


_TOKEN = re.compile(r"(?P<full>(?<![:\d])\d{2}:\d{3}:\d{3}(?![:\d]))"
                    r"|(?P<short>(?<![:\d])\d{3}:\d{3}(?![:\d]))"
                    r"|(?P<bare>(?<![:\d])\d{3}(?![:\d]))")
_JOIN = re.compile(r"^\s*(,|/|&|\bOR\b|\bAND\b)?\s*(,|\bOR\b|\bAND\b)?\s*$", re.I)


def course_codes(text: str, resolve: Resolve) -> tuple[list[str], bool]:
    """Course keys a clause names, and whether every code in it resolved.

    "940:102 OR 121", "07:700:210, 381, 382, OR 384": a bare number directly
    after a code (only a separator between) inherits that code's subject -
    and its unit, when the code stated one. This is the catalog's own
    shorthand. Short codes resolve through `resolve(subject, number)` (the
    course table), never by guessing a unit. A bare number with no code
    before it ("grades of C for 102, 103") is unresolved.
    """
    keys: list[str] = []
    complete = True
    context: tuple[str | None, str] | None = None       # (unit or None, subject)
    last_end = 0
    for m in _TOKEN.finditer(text):
        gap = text[last_end:m.start()]
        if m.group("full"):
            unit, subject, number = m.group("full").split(":")
            keys.append(m.group("full"))
            context = (unit, subject)
        elif m.group("short"):
            subject, number = m.group("short").split(":")
            found = resolve(subject, number)
            if found:
                keys.append(found)
            else:
                complete = False
            context = (None, subject)
        else:
            number = m.group("bare")
            if context is None or not _JOIN.match(gap):
                complete = False
                context = None
                last_end = m.end()
                continue
            unit, subject = context
            found = f"{unit}:{subject}:{number}" if unit else resolve(subject, number)
            if found:
                keys.append(found)
            else:
                complete = False
        last_end = m.end()
    seen: list[str] = []
    for k in keys:
        if k not in seen:
            seen.append(k)
    return seen, complete


def _grade_clause(clause: str, resolve: Resolve) -> dict | None:
    if _ADVISORY.search(clause):
        return None
    for pattern in _ALL_SCOPE:
        m = pattern.search(clause)
        if m:
            return {"grade": m.group("g").upper(), "scope": "all", "courses": [], "text": clause}
    for pattern in _NAMED_SCOPE:
        m = pattern.search(clause)
        if m:
            named, _ = course_codes(m.group("list"), resolve)
            return {"grade": m.group("g").upper(), "scope": "named" if named else "unspecified",
                    "courses": sorted(named), "text": clause}
    return None


def _restatement(clause: str, resolve: Resolve, expression_courses: set[str]) -> dict | None:
    m = _RESTATEMENT.match(clause)
    if not m:
        return None
    body = m.group("body")
    items = [i.strip(" ,.") for i in re.split(r"\bOR\b|,", body, flags=re.I)]
    items = [i for i in items if i]
    alternatives: list[str] = []
    courses: list[str] = []
    for index, item in enumerate(items):
        kind = next((k for k, p in _ALT_KINDS if p.match(item)), None)
        if kind:
            alternatives.append(kind)
            continue
        if re.fullmatch(r"(\d{2}:)?\d{3}:\d{3}|\d{3}", item):
            continue        # resolved below, with the subject context of the whole body
        # The LAST item may carry trailing registration prose ("01:185:201
        # IF CLOSED CONTACT INSTRUCTOR FOR AN SPN"). It is dropped only when it
        # states no condition and names no course; an unrecognized item joined
        # by OR is an alternative and is never dropped.
        tail = re.match(r"^((\d{2}:)?\d{3}:\d{3}|\d{3})\s+(?P<tail>.+)$", item)
        if (index == len(items) - 1 and tail and _LOGISTICS.match(tail.group("tail"))
                and not _SIGNAL.search(tail.group("tail"))
                and not _TOKEN.search(tail.group("tail"))):
            body = body[:body.rindex(tail.group("tail"))]
            continue
        return None         # an item this pattern does not know - not interpreted
    codes, complete = course_codes(body, resolve)
    courses = sorted(codes)
    if not complete or set(courses) != expression_courses:
        return None
    # No alternatives: a plain restatement of the published prerequisite -
    # consistent, adds nothing, caps nothing.
    return {"kinds": sorted(set(alternatives)), "courses": courses, "text": clause}


def interpret(texts: list[str], expression_courses: set[str], resolve: Resolve) -> dict | None:
    """Interpret the condition texts of one offering. None when there are none."""
    texts = [t for t in texts if t]
    if not texts:
        return None
    minimum: list[dict] = []
    alternatives: list[dict] = []
    uninterpreted: set[str] = set()
    corequisite_clauses = 0
    for text in texts:
        for clause in clauses(text):
            marker = COREQ_MARKER.search(clause)
            if marker:
                # The co-requisite part belongs to app.domain.corequisites;
                # only what PRECEDES it is read here.
                corequisite_clauses += 1
                clause = clause[:marker.start()].strip(" ,:-&")
                if not clause:
                    continue
            if not _SIGNAL.search(clause):
                continue                                   # informational prose
            grade = _grade_clause(clause, resolve)
            if grade:
                minimum.append(grade)
                continue
            alt = _restatement(clause, resolve, expression_courses)
            if alt:
                if alt["kinds"]:
                    alternatives.append(alt)
                continue
            kinds = [k for k, p in _CONDITION_KINDS if p.search(clause)] or ["other"]
            uninterpreted.update(kinds)
    out: dict = {"parser_version": CONDITION_PARSER_VERSION, "minimum_grade": None,
                 "alternatives": alternatives, "uninterpreted": sorted(uninterpreted),
                 "corequisite_clauses": corequisite_clauses}
    if minimum:
        grades = {m["grade"] for m in minimum}
        if len(grades) > 1:
            out["uninterpreted"] = sorted(set(out["uninterpreted"]) | {"minimum_grade"})
        else:
            scopes = {m["scope"] for m in minimum}
            scope = "all" if "all" in scopes else "named" if "named" in scopes else "unspecified"
            named = sorted({c for m in minimum for c in m["courses"]})
            out["minimum_grade"] = {"grade": grades.pop(), "scope": scope, "courses": named,
                                    "text": " | ".join(m["text"] for m in minimum)}
    return out


__all__ = ["CONDITION_PARSER_VERSION", "clauses", "clean", "course_codes", "interpret"]
