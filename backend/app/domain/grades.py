"""Canonical grade semantics (Phase 6.4) - the one place grades are interpreted.

Shared by both academic-rule domains, which stay separate:

```
StudentCourse.grade ──► Outcome (this module) ──┬──► Degree Engine          (does it COUNT?)
                                                └──► course eligibility     (may the student TAKE?)
```

Nothing here decides a requirement or a prerequisite. It answers three
narrow questions about ONE recorded outcome, each three-valued:

  * did it earn degree credit?                     `earns_credit`
  * does it meet a minimum grade ("C or better")?  `meets_minimum`
  * does it enter the grade-point average?         `gpa_points`

## Evidence (archived Rutgers catalog, 2026-27, SAS)

"Grades and Records" (catalog page tu9NUN0OlrWop3UP6wEn):
  * symbols and numerical equivalents: A 4.0, B+ 3.5, B 3.0, C+ 2.5, C 2.0,
    D 1.0, F 0.0. There are no minus grades.
  * P/NC: "Pass (equivalent to grades of A, B+, B, C+, and C) or No Credit
    (equivalent to grades of D and F)" - nonnumerical.
  * T grades (TB+ ... TF, TZ): temporary/incomplete; TZ has "no immediate
    effect on a student's grade-point average".
  * W: "withdrawn ... without any evaluation made of coursework".
  * NG: "no immediate effect on the student's grade-point average".
  * H: "not calculated into the student's cumulative grade-point average until
    the final grade is assigned ... Course credits are included ... in the
    total number of degree credits".
  * S/U: used only with the N credit prefix, which means "no credit earned
    toward the degree, no grade computed in the grade-point average".
  * XF: disciplinary F.
"Academic Credit" (K25vg3W9DQ92RnkIWslJ): AP/IB/proficiency and transfer
  credits "are not computed in the cumulative grade-point average"; transfer
  grades "are not posted to the Rutgers transcript".

## Policy where the evidence stops

Anything the evidence does not settle is UNKNOWN, never guessed:

| outcome | earns credit | meets "X or better" | GPA |
|---|---|---|---|
| A..D | yes | ordinal comparison | included, points above |
| F, XF | no | no | F: included 0.0; XF: unknown (not stated) |
| P | yes | yes if X <= C, else unknown | excluded |
| NC | no | no if X >= C; unknown if X = D | excluded |
| W | no | no | excluded |
| T-grades | unknown | unknown | TZ excluded; others unknown |
| NG | unknown | unknown | excluded (no immediate effect) |
| H | yes | unknown | excluded |
| S, U | no (N prefix) | unknown | excluded |
| transfer / exam credit (no grade) | yes | unknown | excluded |
| unrecognized symbol | unknown | unknown | unknown |

Credit prefixes (E/J/K/N/R) are NOT stored by CoursePilot, so anything they
alone decide is UNKNOWN - documented in app.domain.gpa.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

#: Ordered letter grades, best first, with their numerical equivalents.
LETTER_POINTS: dict[str, Decimal] = {
    "A": Decimal("4.0"), "B+": Decimal("3.5"), "B": Decimal("3.0"), "C+": Decimal("2.5"),
    "C": Decimal("2.0"), "D": Decimal("1.0"), "F": Decimal("0.0"),
}
LETTERS: tuple[str, ...] = tuple(LETTER_POINTS)          # A > B+ > B > C+ > C > D > F
_RANK = {g: i for i, g in enumerate(reversed(LETTERS))}  # F=0 ... A=6

#: Where each semantic comes from - returned in evidence so an explanation
#: can cite Rutgers rather than CoursePilot.
POLICY_SOURCES = {
    "grades_and_records": "Rutgers NB catalog 2026-27, SAS Grades and Records "
                          "(/pages/tu9NUN0OlrWop3UP6wEn)",
    "academic_credit": "Rutgers NB catalog 2026-27, SAS Academic Credit "
                       "(/pages/K25vg3W9DQ92RnkIWslJ)",
    "repeating_courses": "Rutgers NB catalog 2026-27, SAS Registration and Course Information, "
                         "Repeating Courses (/pages/LnmAHVj0CVcmVyycHY1b)",
}


class Tri(StrEnum):
    """Three-valued answer shared by every grade question."""

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class OutcomeKind(StrEnum):
    LETTER = "letter"
    PASS = "pass"
    NO_CREDIT = "no_credit"
    WITHDRAWN = "withdrawn"
    TEMPORARY = "temporary"            # T-grades, incl. TZ
    NO_GRADE = "no_grade"              # NG
    HONORS_INTERIM = "honors_interim"  # H
    SATISFACTORY = "satisfactory"      # S (N prefix)
    UNSATISFACTORY = "unsatisfactory"  # U (N prefix)
    DISCIPLINARY_F = "disciplinary_f"  # XF
    CREDIT_WITHOUT_GRADE = "credit_without_grade"   # transfer / exam credit
    IN_PROGRESS = "in_progress"
    NOT_COMPLETED = "not_completed"    # planned
    UNRECOGNIZED = "unrecognized"


@dataclass(frozen=True, slots=True)
class Outcome:
    """One recorded attempt's result, as Rutgers defines it."""

    kind: OutcomeKind
    symbol: str | None          # the grade exactly as recorded (normalized case/space)
    letter: str | None = None   # for LETTER, and the letter floor of a T grade

    @property
    def is_final(self) -> bool:
        return self.kind not in (OutcomeKind.TEMPORARY, OutcomeKind.NO_GRADE,
                                 OutcomeKind.IN_PROGRESS, OutcomeKind.NOT_COMPLETED,
                                 OutcomeKind.HONORS_INTERIM, OutcomeKind.UNRECOGNIZED)


def is_letter(grade: str | None) -> bool:
    return (grade or "").strip().upper() in LETTER_POINTS


def outcome(status: str, grade: str | None, credit_origin: str = "rutgers") -> Outcome:
    """Classify one StudentCourse row. Deterministic; never raises."""
    symbol = (grade or "").strip().upper() or None
    if status == "planned":
        return Outcome(OutcomeKind.NOT_COMPLETED, symbol)
    if status == "in_progress":
        return Outcome(OutcomeKind.IN_PROGRESS, symbol)
    if credit_origin in ("transfer", "exam_credit"):
        # A Rutgers equivalency without a Rutgers grade. Any symbol recorded
        # with it is the other institution's/exam's, not a Rutgers grade.
        return Outcome(OutcomeKind.CREDIT_WITHOUT_GRADE, symbol)
    if symbol is None:
        return Outcome(OutcomeKind.UNRECOGNIZED, None)
    if symbol in LETTER_POINTS:
        return Outcome(OutcomeKind.LETTER, symbol, symbol)
    simple = {"P": OutcomeKind.PASS, "PA": OutcomeKind.PASS, "NC": OutcomeKind.NO_CREDIT,
              "W": OutcomeKind.WITHDRAWN, "NG": OutcomeKind.NO_GRADE,
              "H": OutcomeKind.HONORS_INTERIM, "S": OutcomeKind.SATISFACTORY,
              "U": OutcomeKind.UNSATISFACTORY, "XF": OutcomeKind.DISCIPLINARY_F}
    if symbol in simple:
        return Outcome(simple[symbol], symbol)
    if symbol == "TZ":
        return Outcome(OutcomeKind.TEMPORARY, symbol)
    if symbol.startswith("T") and symbol[1:] in LETTER_POINTS:
        return Outcome(OutcomeKind.TEMPORARY, symbol, symbol[1:])
    return Outcome(OutcomeKind.UNRECOGNIZED, symbol)


# --------------------------------------------------------------------------
# the three questions
# --------------------------------------------------------------------------


def earns_credit(o: Outcome) -> Tri:
    """Did this attempt earn degree credit (completion, not quality)?"""
    if o.kind is OutcomeKind.LETTER:
        return Tri.NO if o.letter == "F" else Tri.YES
    if o.kind in (OutcomeKind.PASS, OutcomeKind.HONORS_INTERIM, OutcomeKind.CREDIT_WITHOUT_GRADE):
        return Tri.YES
    if o.kind in (OutcomeKind.NO_CREDIT, OutcomeKind.WITHDRAWN, OutcomeKind.SATISFACTORY,
                  OutcomeKind.UNSATISFACTORY, OutcomeKind.DISCIPLINARY_F,
                  OutcomeKind.NOT_COMPLETED):
        return Tri.NO
    return Tri.UNKNOWN          # in progress, temporary, NG, unrecognized


def validate_minimum(minimum: str) -> str:
    m = (minimum or "").strip().upper()
    if m not in LETTER_POINTS or m == "F":
        raise ValueError(f"not a minimum grade: {minimum!r} (expected one of A..D)")
    return m


def meets_minimum(o: Outcome, minimum: str) -> Tri:
    """Does this attempt satisfy "`minimum` or better"?"""
    m = validate_minimum(minimum)
    if o.kind is OutcomeKind.LETTER:
        return Tri.YES if _RANK[o.letter] >= _RANK[m] else Tri.NO
    if o.kind is OutcomeKind.PASS:
        # Pass = A..C. Meets any minimum at or below C; above C it is unknown.
        return Tri.YES if _RANK[m] <= _RANK["C"] else Tri.UNKNOWN
    if o.kind is OutcomeKind.NO_CREDIT:
        # No Credit = D or F. Fails any minimum above D; at D it is unknown.
        return Tri.NO if _RANK[m] > _RANK["D"] else Tri.UNKNOWN
    if o.kind in (OutcomeKind.WITHDRAWN, OutcomeKind.DISCIPLINARY_F, OutcomeKind.NOT_COMPLETED,
                  OutcomeKind.UNSATISFACTORY):
        return Tri.NO
    return Tri.UNKNOWN


def at_most(o: Outcome, grade: str) -> Tri:
    """Is this attempt's grade at or below `grade`? (Grade quotas: "a grade of D".)"""
    g = (grade or "").strip().upper()
    if g not in LETTER_POINTS:
        raise ValueError(f"not a letter grade: {grade!r}")
    if o.kind is OutcomeKind.LETTER:
        return Tri.YES if _RANK[o.letter] <= _RANK[g] else Tri.NO
    if o.kind is OutcomeKind.PASS:            # one of A..C
        if g == "A":
            return Tri.YES
        return Tri.NO if _RANK[g] < _RANK["C"] else Tri.UNKNOWN
    if o.kind is OutcomeKind.NO_CREDIT:       # one of D, F
        return Tri.YES if _RANK[g] >= _RANK["D"] else Tri.UNKNOWN
    if o.kind is OutcomeKind.DISCIPLINARY_F:  # an F
        return Tri.YES
    return Tri.UNKNOWN


def gpa_points(o: Outcome) -> tuple[Tri, Decimal | None]:
    """(included in the GPA?, numerical equivalent when included)."""
    if o.kind is OutcomeKind.LETTER:
        return Tri.YES, LETTER_POINTS[o.letter]
    if o.kind in (OutcomeKind.PASS, OutcomeKind.NO_CREDIT, OutcomeKind.WITHDRAWN,
                  OutcomeKind.NO_GRADE, OutcomeKind.HONORS_INTERIM, OutcomeKind.SATISFACTORY,
                  OutcomeKind.UNSATISFACTORY, OutcomeKind.CREDIT_WITHOUT_GRADE,
                  OutcomeKind.NOT_COMPLETED, OutcomeKind.IN_PROGRESS):
        return Tri.NO, None
    if o.kind is OutcomeKind.TEMPORARY and o.symbol == "TZ":
        return Tri.NO, None
    return Tri.UNKNOWN, None    # other T grades, XF, unrecognized


def is_c_or_better_final(o: Outcome) -> Tri:
    """The SAS repeated-course trigger: a final outcome of C or better (or Pass)."""
    if o.kind is OutcomeKind.LETTER:
        return Tri.YES if _RANK[o.letter] >= _RANK["C"] else Tri.NO
    if o.kind is OutcomeKind.PASS:
        return Tri.YES
    if o.kind in (OutcomeKind.NO_CREDIT, OutcomeKind.WITHDRAWN, OutcomeKind.DISCIPLINARY_F,
                  OutcomeKind.UNSATISFACTORY, OutcomeKind.NOT_COMPLETED):
        return Tri.NO
    return Tri.UNKNOWN


__all__ = ["LETTERS", "LETTER_POINTS", "POLICY_SOURCES", "Outcome", "OutcomeKind", "Tri",
           "at_most", "earns_credit", "gpa_points", "is_c_or_better_final", "is_letter",
           "meets_minimum", "outcome", "validate_minimum"]
