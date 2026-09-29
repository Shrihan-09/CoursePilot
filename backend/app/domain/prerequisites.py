"""Course prerequisites: intermediate representation, parser, evaluator (Phase 6.2).

A prerequisite answers a different question from a degree requirement:

    degree requirement   "What must this student complete to GRADUATE?"   (Degree Engine)
    prerequisite         "What must this student complete before TAKING this course?"

They share no code path. A course can be a degree requirement and have
prerequisites; the two are evaluated by different engines for different
reasons, and a planner will need both.

Pure and deterministic: no database, no network, no model. Rutgers publishes
the expression; this module interprets the subset it can interpret SAFELY and
marks everything else UNKNOWN. An unknown prerequisite is never satisfied.

## The grammar - measured, not assumed

Built from every `preReqNotes` string in the five archived SOC terms
(1,354 distinct strings, 4,133 occurrences):

```
(01:013:141 ELEMENTARY ARABIC II )<em> OR </em>(01:013:145 ACCELERATED ARABIC )
((01:119:116 GENERAL BIOLOGY II  and 01:119:117 BIOLOGICAL RESEARCH LABORATORY ) or (01:119:102 GENERAL BIOLOGY ))
Any Two Course from the following: (01:615:305 SYNTAX )    (01:615:315 PHONOLOGY )
Any Course EQUAL or GREATER Than: (01:640:112 PRECALCULUS PART II )
```

  * operators are `<em> OR </em>` / `<em> AND </em>` between groups, and
    lower-case `or` / `and` inside them (the only <em> contents observed);
  * every operand is a course code followed by its title in UPPER CASE -
    measured: no title in the corpus contains a lower-case letter - and
    titles legitimately contain the words AND and OR ("CALCULUS I FOR THE
    LIFE AND SOCIAL SCIENCES").

So operator recognition is CASE-SENSITIVE by design: lower-case `or`/`and`
or an <em>-wrapped operator is an operator; upper-case AND/OR outside <em> is
title text. Treating the words case-insensitively would split real titles.

## What is NOT interpreted

  * "Any Course EQUAL or GREATER Than: (X)" - a course-level rule whose
    ordering Rutgers does not define in the data -> UNSUPPORTED;
  * mixed `and`/`or` at one nesting level without parentheses - precedence
    is not published, so it is not guessed -> UNSUPPORTED;
  * anything that does not tokenize -> MALFORMED / UNKNOWN.

Minimum-grade conditions are NOT in `preReqNotes` at all; they appear in
SOC `courseNotes` ("Student needs C or better in all prerequisites"). They
are carried beside the expression as an unmodeled condition, which caps any
evaluation at UNKNOWN (see `evaluate`).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Union

#: Bump when parsing output can change for identical input. Stored with every
#: parsed prerequisite so an old interpretation is identifiable.
PARSER_VERSION = "1"

COURSE_KEY = re.compile(r"\d{2}:\d{3}:\d{3}")


class Classification(StrEnum):
    PARSED = "parsed"
    UNSUPPORTED_MINIMUM_COURSE_LEVEL = "unsupported_minimum_course_level"
    UNSUPPORTED_AMBIGUOUS_PRECEDENCE = "unsupported_ambiguous_precedence"
    MALFORMED = "malformed"
    UNKNOWN = "unknown"

    @property
    def is_parsed(self) -> bool:
        return self is Classification.PARSED


# --------------------------------------------------------------------------
# intermediate representation
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CourseReq:
    """A single course. Its identity only - never titles, credits or offerings."""

    course_key: str


@dataclass(frozen=True, slots=True)
class AllOf:
    children: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class AnyOf:
    children: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class AtLeast:
    """ "Any Two Course from the following: ..." - N of the listed courses."""

    n: int
    children: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Unsupported:
    """A condition CoursePilot does not interpret. Always evaluates UNKNOWN."""

    reason: str
    text: str
    references: tuple[str, ...] = ()


Expr = Union[CourseReq, AllOf, AnyOf, AtLeast, Unsupported]


def course_keys(expr: Expr) -> list[str]:
    """Every course referenced, in canonical order, without duplicates."""
    out: list[str] = []

    def walk(e: Expr) -> None:
        if isinstance(e, CourseReq):
            if e.course_key not in out:
                out.append(e.course_key)
        elif isinstance(e, Unsupported):
            for k in e.references:
                if k not in out:
                    out.append(k)
        else:
            for c in e.children:
                walk(c)

    walk(expr)
    return out


# --------------------------------------------------------------------------
# canonicalization
# --------------------------------------------------------------------------


def canonicalize(expr: Expr) -> Expr:
    """Structural normal form.

      * nested nodes of the same kind are flattened: (A and (B and C)) -> (A and B and C)
      * duplicate children are removed (SOC repeats references)
      * children are sorted by their canonical text, so order is irrelevant
      * a single-child AND/OR collapses to the child
    """
    if isinstance(expr, CourseReq | Unsupported):
        return expr
    if isinstance(expr, AtLeast):
        kids = sorted({to_text(canonicalize(c)): canonicalize(c) for c in expr.children}.items())
        return AtLeast(expr.n, tuple(k for _, k in kids))
    kind = type(expr)
    flat: dict[str, Expr] = {}
    for child in expr.children:
        child = canonicalize(child)
        items = child.children if isinstance(child, kind) else (child,)
        for item in items:
            flat.setdefault(to_text(item), item)
    children = tuple(flat[k] for k in sorted(flat))
    return children[0] if len(children) == 1 else kind(children)


def to_text(expr: Expr) -> str:
    """Canonical text. Parses back to the same structure with `parse`."""
    if isinstance(expr, CourseReq):
        return expr.course_key
    if isinstance(expr, Unsupported):
        return f"UNSUPPORTED[{expr.reason}]"
    if isinstance(expr, AtLeast):
        words = {v: k for k, v in _COUNT_WORDS.items()}
        listed = " ".join(f"({to_text(c)})" for c in expr.children)
        return f"Any {words[expr.n]} Course from the following: {listed}"
    op = " and " if isinstance(expr, AllOf) else " or "
    return "(" + op.join(to_text(c) for c in expr.children) + ")"


def to_json(expr: Expr) -> dict:
    if isinstance(expr, CourseReq):
        return {"course": expr.course_key}
    if isinstance(expr, Unsupported):
        return {"unsupported": expr.reason, "text": expr.text,
                "references": list(expr.references)}
    if isinstance(expr, AtLeast):
        return {"at_least": expr.n, "of": [to_json(c) for c in expr.children]}
    key = "all_of" if isinstance(expr, AllOf) else "any_of"
    return {key: [to_json(c) for c in expr.children]}


def from_json(data: dict) -> Expr:
    if "course" in data:
        return CourseReq(data["course"])
    if "unsupported" in data:
        return Unsupported(data["unsupported"], data.get("text", ""),
                           tuple(data.get("references", ())))
    if "at_least" in data:
        return AtLeast(int(data["at_least"]), tuple(from_json(c) for c in data["of"]))
    if "all_of" in data:
        return AllOf(tuple(from_json(c) for c in data["all_of"]))
    if "any_of" in data:
        return AnyOf(tuple(from_json(c) for c in data["any_of"]))
    raise ValueError(f"not a prerequisite expression: {sorted(data)}")


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

_COUNT_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5, "Six": 6}
_MIN_LEVEL = re.compile(r"^\s*Any Course EQUAL or GREATER Than:\s*", re.I)
_N_OF = re.compile(r"^\s*Any (" + "|".join(_COUNT_WORDS) + r") Courses? from the following:\s*")
_EM_OP = re.compile(r"<em>\s*(AND|OR)\s*</em>", re.I)
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class ParseResult:
    classification: Classification
    expression: Expr | None
    references: tuple[str, ...]
    detail: str | None = None

    @property
    def canonical_text(self) -> str | None:
        return None if self.expression is None else to_text(self.expression)


class _Malformed(Exception):
    pass


class _Ambiguous(Exception):
    pass


def _tokenize(text: str) -> list[tuple[str, str]]:
    """Tokens: ('(' | ')' | 'AND' | 'OR' | 'COURSE', value). Titles dropped."""
    text = _EM_OP.sub(lambda m: f" \x00{m.group(1).upper()}\x00 ", text)
    if _TAG.search(text):
        raise _Malformed("unexpected markup")
    text = html.unescape(text)
    tokens: list[tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch in "()":
            tokens.append((ch, ch))
            i += 1
        elif ch == "\x00":
            end = text.index("\x00", i + 1)
            tokens.append((text[i + 1:end], text[i + 1:end]))
            i = end + 1
        elif m := COURSE_KEY.match(text, i):
            tokens.append(("COURSE", m.group(0)))
            i = m.end()
            # Title: upper-case text up to the next boundary. Measured: no
            # title contains a lower-case letter, so the first lower-case
            # word ends it - and that word must be an operator.
            while i < n and text[i] not in ")\x00" and not text[i].islower():
                if text[i] == "(":
                    # Titles contain their own parentheses: "PHYSICAL THERAPY
                    # (PT)", "(K-12)", "(CSW) SPECIALIZATION". A parenthesized
                    # run is TITLE text only if it closes before any other
                    # bracket and holds no course code, no lower-case operator
                    # and no <em> operator. Otherwise it opens a group, which
                    # the parser will then accept or reject.
                    close = text.find(")", i)
                    inner = text[i + 1:close] if close != -1 else ""
                    if (close == -1 or "(" in inner or "\x00" in inner
                            or COURSE_KEY.search(inner) or any(c.islower() for c in inner)):
                        break
                    i = close + 1
                    continue
                if COURSE_KEY.match(text, i) and text[i - 1].isspace():
                    break
                i += 1
        elif m := re.compile(r"(and|or)\b").match(text, i):
            tokens.append((m.group(1).upper(), m.group(1)))
            i = m.end()
        else:
            raise _Malformed(f"unrecognised text at {i}: {text[i:i + 30]!r}")
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos][0] if self.pos < len(self.tokens) else None

    def take(self, kind: str) -> str:
        if self.peek() != kind:
            raise _Malformed(f"expected {kind}, found {self.peek()}")
        value = self.tokens[self.pos][1]
        self.pos += 1
        return value

    def expr(self) -> Expr:
        terms = [self.term()]
        ops: set[str] = set()
        while (op := self.peek()) in ("AND", "OR"):
            self.take(op)
            ops.add(op)
            terms.append(self.term())
        if len(ops) > 1:
            # Rutgers does not publish precedence; guessing it could turn
            # "A or B and C" into the wrong requirement.
            raise _Ambiguous("mixed and/or without grouping")
        if not ops:
            return terms[0]
        return AllOf(tuple(terms)) if ops == {"AND"} else AnyOf(tuple(terms))

    def term(self) -> Expr:
        if self.peek() == "(":
            self.take("(")
            inner = self.expr()
            self.take(")")
            return inner
        if self.peek() == "COURSE":
            return CourseReq(self.take("COURSE"))
        raise _Malformed(f"expected a course or '(', found {self.peek()}")


def _parse_boolean(text: str) -> Expr:
    parser = _Parser(_tokenize(text))
    if parser.peek() is None:
        raise _Malformed("empty expression")
    expr = parser.expr()
    if parser.peek() is not None:
        raise _Malformed(f"trailing tokens from {parser.peek()}")
    return expr


def parse(raw: str | None) -> ParseResult | None:
    """Interpret one SOC `preReqNotes` string. None means no prerequisite text.

    Never raises on bad input: every failure is a classification, and the
    raw text is always kept by the caller.
    """
    if raw is None or not raw.strip():
        return None
    references = tuple(dict.fromkeys(COURSE_KEY.findall(raw)))

    if _MIN_LEVEL.match(raw):
        # The referenced course survives as an identity, but "equal or
        # greater" has no ordering CoursePilot can apply. UNKNOWN, always.
        node = Unsupported("minimum_course_level", raw.strip(), references)
        return ParseResult(Classification.UNSUPPORTED_MINIMUM_COURSE_LEVEL, node, references,
                           "course-level ordering is not defined in the data")

    if m := _N_OF.match(raw):
        n = _COUNT_WORDS[m.group(1)]
        try:
            groups = _Parser(_tokenize(raw[m.end():]))
            children = []
            while groups.peek() is not None:
                children.append(groups.term())
        except _Malformed as exc:
            return ParseResult(Classification.MALFORMED, None, references, str(exc))
        if len(children) < n:
            return ParseResult(Classification.MALFORMED, None, references,
                               f"needs {n} of {len(children)} listed courses")
        return ParseResult(Classification.PARSED,
                           canonicalize(AtLeast(n, tuple(children))), references)

    try:
        expr = _parse_boolean(raw)
    except _Ambiguous as exc:
        node = Unsupported("ambiguous_precedence", raw.strip(), references)
        return ParseResult(Classification.UNSUPPORTED_AMBIGUOUS_PRECEDENCE, node,
                           references, str(exc))
    except _Malformed as exc:
        kind = Classification.MALFORMED if references else Classification.UNKNOWN
        return ParseResult(kind, None, references, str(exc))
    return ParseResult(Classification.PARSED, canonicalize(expr), references)


# --------------------------------------------------------------------------
# published conditions outside the expression
# --------------------------------------------------------------------------

_PREREQ_WORD = re.compile(r"pre-?req|prerequisite", re.I)
_CONDITION_KINDS: tuple[tuple[str, re.Pattern], ...] = (
    ("minimum_grade", re.compile(
        r"\bgrades?\b|\b[A-D][+]? or (better|higher)\b|\bbelow an? '?[A-D]\b", re.I)),
    ("placement", re.compile(r"placement", re.I)),
    ("permission", re.compile(r"permission|\bperm\b|consent", re.I)),
    ("corequisite", re.compile(r"co-?req", re.I)),
    ("program_restriction", re.compile(r"\bmajors?\b|\bminors?\b|declared|restricted to", re.I)),
)


def condition_kinds(note: str | None) -> tuple[str, ...]:
    """Kinds of prerequisite condition a SOC note states, or () if none.

    Measured: grade, placement, permission, co-requisite and major
    conditions appear in `courseNotes`, never in `preReqNotes`. A note that
    mentions prerequisites is ALWAYS a condition - classified here only for
    reporting; it is never interpreted. "other" covers prose that mentions
    prerequisites without a recognised kind.
    """
    if not note or not _PREREQ_WORD.search(note):
        return ()
    text = html.unescape(_TAG.sub(" ", note))
    return tuple(k for k, p in _CONDITION_KINDS if p.search(text)) or ("other",)


# --------------------------------------------------------------------------
# evaluation - three-valued, with evidence
# --------------------------------------------------------------------------


class PrereqStatus(StrEnum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNKNOWN = "unknown"


class AttemptState(StrEnum):
    """A student's best standing in one course, for prerequisite purposes."""

    PASSED = "passed"              # completed with a non-failing grade
    IN_PROGRESS = "in_progress"    # the outcome is not known yet
    NOT_PASSED = "not_passed"      # failed, withdrawn, planned or never taken


@dataclass(slots=True)
class Evaluation:
    status: PrereqStatus
    required_courses: list[str] = field(default_factory=list)
    satisfied_courses: list[str] = field(default_factory=list)
    missing_courses: list[str] = field(default_factory=list)
    pending_courses: list[str] = field(default_factory=list)
    unknown_reasons: list[str] = field(default_factory=list)


def _combine_all(states: list[PrereqStatus]) -> PrereqStatus:
    """Kleene AND: one UNSATISFIED decides; otherwise any UNKNOWN is UNKNOWN."""
    if PrereqStatus.UNSATISFIED in states:
        return PrereqStatus.UNSATISFIED
    if PrereqStatus.UNKNOWN in states:
        return PrereqStatus.UNKNOWN
    return PrereqStatus.SATISFIED


def _combine_any(states: list[PrereqStatus]) -> PrereqStatus:
    """Kleene OR: one SATISFIED decides; otherwise any UNKNOWN is UNKNOWN."""
    if PrereqStatus.SATISFIED in states:
        return PrereqStatus.SATISFIED
    if PrereqStatus.UNKNOWN in states:
        return PrereqStatus.UNKNOWN
    return PrereqStatus.UNSATISFIED


def _combine_at_least(n: int, states: list[PrereqStatus]) -> PrereqStatus:
    sat = states.count(PrereqStatus.SATISFIED)
    unknown = states.count(PrereqStatus.UNKNOWN)
    if sat >= n:
        return PrereqStatus.SATISFIED
    if sat + unknown < n:
        return PrereqStatus.UNSATISFIED
    return PrereqStatus.UNKNOWN


def evaluate(
    expr: Expr,
    history: dict[str, AttemptState],
    *,
    unmodeled_conditions: tuple[str, ...] = (),
) -> Evaluation:
    """Evaluate a prerequisite expression against a student's course history.

    `history` maps course key -> the student's best AttemptState in it; a
    missing key means never taken.

    `unmodeled_conditions` are published conditions CoursePilot does not
    interpret (e.g. a minimum-grade note). They only ever LOWER confidence: a
    result that would be SATISFIED becomes UNKNOWN, because the condition
    might not be met; an UNSATISFIED result stays UNSATISFIED, because a
    further restriction cannot make a failed expression pass.
    """
    out = Evaluation(status=PrereqStatus.UNKNOWN)

    def walk(e: Expr) -> PrereqStatus:
        if isinstance(e, CourseReq):
            if e.course_key not in out.required_courses:
                out.required_courses.append(e.course_key)
            state = history.get(e.course_key, AttemptState.NOT_PASSED)
            if state is AttemptState.PASSED:
                _add(out.satisfied_courses, e.course_key)
                return PrereqStatus.SATISFIED
            if state is AttemptState.IN_PROGRESS:
                _add(out.pending_courses, e.course_key)
                _add(out.unknown_reasons, f"in_progress:{e.course_key}")
                return PrereqStatus.UNKNOWN
            _add(out.missing_courses, e.course_key)
            return PrereqStatus.UNSATISFIED
        if isinstance(e, Unsupported):
            _add(out.unknown_reasons, f"unsupported:{e.reason}")
            return PrereqStatus.UNKNOWN
        states = [walk(c) for c in e.children]
        if isinstance(e, AllOf):
            return _combine_all(states)
        if isinstance(e, AnyOf):
            return _combine_any(states)
        return _combine_at_least(e.n, states)

    out.status = walk(expr)
    if unmodeled_conditions and out.status is PrereqStatus.SATISFIED:
        out.status = PrereqStatus.UNKNOWN
        for condition in unmodeled_conditions:
            _add(out.unknown_reasons, f"unmodeled_condition:{condition}")
    return out


def _add(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


__all__ = [
    "PARSER_VERSION",
    "AllOf",
    "AnyOf",
    "AtLeast",
    "AttemptState",
    "Classification",
    "CourseReq",
    "Evaluation",
    "Expr",
    "ParseResult",
    "PrereqStatus",
    "Unsupported",
    "canonicalize",
    "course_keys",
    "evaluate",
    "from_json",
    "parse",
    "to_json",
    "to_text",
]
