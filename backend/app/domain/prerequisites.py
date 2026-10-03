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
SOC `courseNotes` ("Student needs C or better in all prerequisites"). Phase
6.2 carried them beside the expression as an unmodeled condition capping any
evaluation at UNKNOWN. Phase 6.4 interprets the observed forms
(app.domain.conditions) and evaluates them here as a `GradeCondition`;
whatever is still not interpreted keeps capping exactly as before.

## Phase 6.4 additions

  * `ConcurrentReq` - a co-requisite leaf ("may be taken in the same term"),
    produced only by app.domain.corequisites and evaluated against a
    proposed term (`term_code`, `proposed`).
  * history values may be attempt LISTS (app.domain.attempts.Attempt), which
    carry grades; AttemptState values keep their Phase 6.2 meaning.
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
class ConcurrentReq:
    """A co-requisite: the course taken in the SAME term (Phase 6.4).

    `prior_allowed`: True  - completing it earlier also satisfies ("PRE OR COREQ")
                     None  - the source does not say ("COREQ: X") -> UNKNOWN
                     False - only the same term ("MUST BE TAKEN CONCURRENTLY")
    """

    course_key: str
    prior_allowed: bool | None


@dataclass(frozen=True, slots=True)
class Unsupported:
    """A condition CoursePilot does not interpret. Always evaluates UNKNOWN."""

    reason: str
    text: str
    references: tuple[str, ...] = ()


Expr = Union[CourseReq, ConcurrentReq, AllOf, AnyOf, AtLeast, Unsupported]


def course_keys(expr: Expr) -> list[str]:
    """Every course referenced, in canonical order, without duplicates."""
    out: list[str] = []

    def walk(e: Expr) -> None:
        if isinstance(e, CourseReq | ConcurrentReq):
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
    if isinstance(expr, CourseReq | ConcurrentReq | Unsupported):
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
    if isinstance(expr, ConcurrentReq):
        mode = {True: "before or same term", None: "same term; earlier unspecified",
                False: "same term only"}[expr.prior_allowed]
        return f"CONCURRENT[{expr.course_key}: {mode}]"
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
    if isinstance(expr, ConcurrentReq):
        return {"concurrent": expr.course_key, "prior_allowed": expr.prior_allowed}
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
    if "concurrent" in data:
        return ConcurrentReq(data["concurrent"], data.get("prior_allowed"))
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
    #: Phase 6.4: one entry per course whose GRADE decided (or left
    #: undecided) its leaf - course, required grade, earned grade, result.
    grade_checks: list[dict] = field(default_factory=list)
    #: Phase 6.4: co-requisite leaves and how each was met.
    concurrent_checks: list[dict] = field(default_factory=list)
    #: Phase 6.4: the status BEFORE unmodeled conditions capped it, so a
    #: caller combining this with another rule can re-apply the cap after.
    uncapped_status: PrereqStatus | None = None
    unmodeled_conditions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GradeCondition:
    """An interpreted minimum-grade prerequisite condition (app.domain.conditions).

    scope "all":         every prerequisite course needs `minimum`
    scope "named":       the `courses` named need it; the others are ambiguous
    scope "unspecified": every course is ambiguous
    Ambiguous: a grade at/above `minimum` satisfies under any reading; a passing
    grade below it is UNKNOWN.
    """

    minimum: str
    scope: str
    courses: frozenset[str] = frozenset()

    def applies(self, course_key: str) -> str:
        if self.scope == "all" or (self.scope == "named" and course_key in self.courses):
            return "strict"
        return "ambiguous"


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
    history: dict,
    *,
    unmodeled_conditions: tuple[str, ...] = (),
    grade_condition: GradeCondition | None = None,
    term_code: str | None = None,
    proposed: frozenset[str] = frozenset(),
) -> Evaluation:
    """Evaluate a prerequisite (or co-requisite) expression for a student.

    `history` maps course key -> the student's AttemptState (Phase 6.2) or
    list of Attempts (Phase 6.4, carries grades); a missing key means never
    taken.

    `unmodeled_conditions` are published conditions CoursePilot does not
    interpret. They only ever LOWER confidence: SATISFIED becomes UNKNOWN; an
    UNSATISFIED result stays UNSATISFIED, because a further restriction cannot
    make a failed expression pass.

    `grade_condition`: an interpreted minimum grade (see GradeCondition).
    `term_code` / `proposed`: the term being asked about and the courses
    proposed for it - only co-requisite leaves read them.
    """
    from app.domain.attempts import prerequisite_grade, term_order
    from app.domain.grades import Tri

    out = Evaluation(status=PrereqStatus.UNKNOWN)
    to_status = {Tri.YES: PrereqStatus.SATISFIED, Tri.NO: PrereqStatus.UNSATISFIED,
                 Tri.UNKNOWN: PrereqStatus.UNKNOWN}

    def record(key: str, status: PrereqStatus, reason: str | None = None) -> PrereqStatus:
        if status is PrereqStatus.SATISFIED:
            _add(out.satisfied_courses, key)
        elif status is PrereqStatus.UNSATISFIED:
            _add(out.missing_courses, key)
        else:
            _add(out.pending_courses, key)
            if reason:
                _add(out.unknown_reasons, f"{reason}:{key}")
        return status

    def course_leaf(key: str) -> PrereqStatus:
        value = history.get(key)
        rule = grade_condition.applies(key) if grade_condition else None
        if value is None or isinstance(value, AttemptState):
            state = value or AttemptState.NOT_PASSED
            if state is AttemptState.PASSED:
                if rule:      # passed, but no grade on hand to compare
                    out.grade_checks.append({"course": key, "required_grade":
                                             grade_condition.minimum, "scope": rule,
                                             "earned_grade": None, "result": "unknown",
                                             "reason": "no_grade_information"})
                    return record(key, PrereqStatus.UNKNOWN, "no_grade_information")
                return record(key, PrereqStatus.SATISFIED)
            if state is AttemptState.IN_PROGRESS:
                return record(key, PrereqStatus.UNKNOWN, "in_progress")
            return record(key, PrereqStatus.UNSATISFIED)
        attempts = value
        base = prerequisite_grade(attempts, None)
        if not rule:
            return record(key, to_status[base.status],
                          "in_progress" if base.status is Tri.UNKNOWN else None)
        check = prerequisite_grade(attempts, grade_condition.minimum)
        if rule == "strict":
            status = to_status[check.status]
            reason = check.reason
        elif check.status is Tri.YES:
            status, reason = PrereqStatus.SATISFIED, "meets_minimum"
        elif base.status is Tri.YES:
            status, reason = PrereqStatus.UNKNOWN, "grade_scope_ambiguous"
        else:
            status, reason = to_status[base.status], base.reason
        used = check.attempt or base.attempt
        out.grade_checks.append({
            "course": key, "required_grade": grade_condition.minimum, "scope": rule,
            "earned_grade": used.grade if used else None,
            "term": used.term_code if used else None,
            "credit_origin": used.credit_origin if used else None,
            "result": status.value, "reason": reason})
        return record(key, status, reason if status is PrereqStatus.UNKNOWN else None)

    def concurrent_leaf(e: ConcurrentReq) -> PrereqStatus:
        key = e.course_key
        entry = {"course": key, "prior_allowed": e.prior_allowed}
        if key in proposed:
            entry["met_by"] = "proposed_same_term"
            out.concurrent_checks.append(entry)
            return record(key, PrereqStatus.SATISFIED)
        value = history.get(key)
        if value is None or isinstance(value, AttemptState):
            if value is AttemptState.PASSED:
                done = Tri.YES
            elif value is AttemptState.IN_PROGRESS:
                done = Tri.UNKNOWN
            else:
                done = Tri.NO
            same_term = False
        else:
            target = term_order(term_code)
            same_term = any(a.status == "in_progress" and a.term_code == term_code
                            for a in value)
            earlier = [a for a in value if target is not None
                       and term_order(a.term_code) is not None
                       and term_order(a.term_code) < target]
            done = prerequisite_grade(earlier, None).status if earlier else Tri.NO
            # Completing it EARLIER makes it a prerequisite course, so an
            # interpreted minimum grade governs that completion too ("A grade
            # below a 'C' in a prerequisite course will not satisfy prereq").
            rule = grade_condition.applies(key) if grade_condition else None
            if earlier and rule:
                meets = prerequisite_grade(earlier, grade_condition.minimum).status
                if rule == "strict" or meets is Tri.YES:
                    done = meets
                elif done is Tri.YES:
                    done = Tri.UNKNOWN
                entry["required_grade"] = grade_condition.minimum
            if target is None and value:
                done = Tri.UNKNOWN
        if same_term:
            entry["met_by"] = "enrolled_same_term"
            out.concurrent_checks.append(entry)
            return record(key, PrereqStatus.SATISFIED)
        if done is Tri.YES:
            entry["met_by"] = "completed_earlier"
            out.concurrent_checks.append(entry)
            if e.prior_allowed is True:
                return record(key, PrereqStatus.SATISFIED)
            if e.prior_allowed is None:
                return record(key, PrereqStatus.UNKNOWN, "corequisite_prior_completion_unspecified")
            return record(key, PrereqStatus.UNSATISFIED)
        if done is Tri.UNKNOWN and e.prior_allowed is not False:
            entry["met_by"] = "pending"
            out.concurrent_checks.append(entry)
            return record(key, PrereqStatus.UNKNOWN, "in_progress")
        entry["met_by"] = None
        out.concurrent_checks.append(entry)
        return record(key, PrereqStatus.UNSATISFIED)

    def walk(e: Expr) -> PrereqStatus:
        if isinstance(e, CourseReq):
            if e.course_key not in out.required_courses:
                out.required_courses.append(e.course_key)
            return course_leaf(e.course_key)
        if isinstance(e, ConcurrentReq):
            if e.course_key not in out.required_courses:
                out.required_courses.append(e.course_key)
            return concurrent_leaf(e)
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
    out.uncapped_status = out.status
    out.unmodeled_conditions = tuple(unmodeled_conditions)
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
    "ConcurrentReq",
    "CourseReq",
    "Evaluation",
    "Expr",
    "GradeCondition",
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
