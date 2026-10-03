"""Repeated attempts: which attempt answers which question (Phase 6.4).

A course can appear on a record more than once. "Latest wins" and "highest
wins" are both wrong for some question, so each question gets its own,
documented policy - and each is applied to the whole attempt history, never to
a pre-chosen single row.

## Degree credit (Degree Engine) - SAS "Repeating Courses"

  "Students may not repeat, for degree credit, courses bearing the same or
   equivalent course numbers ... If [a student has earned a grade of C or
   better] and choose[s] to repeat the course, it must be repeated for E
   credit." E credit: "no degree credit earned and grade does not compute in
   the GPA". For AP/exam credit: a later Rutgers enrollment "will be
   E-prefixed".

So, in term order, every attempt AFTER the first final outcome of C or better
(or Pass, or exam credit) is E credit and is excluded from degree questions.
Before that point (F, D, NC, W) attempts remain eligible: a D earns credit,
and a later qualifying attempt is a legitimate repeat.

  * degree credit / allocation: the latest credit-earning eligible attempt;
    failing that, the latest provisional one (in progress, temporary grade).
  * degree minimum grade: satisfied when an ELIGIBLE attempt meets it. A
    "B or better" requirement is NOT met by a B earned after a C, because
    that B is E credit.
  * Uncertainty is carried, not resolved: a non-final attempt (T grade, NG,
    unrecognized symbol) or a transfer credit before a later attempt means
    CoursePilot cannot tell whether the later attempt is E credit.

## Prerequisites (course eligibility)

The question is "has the student earned X (with grade G or better)?". Any
attempt that earned it answers yes - an E-credit repeat does not unearn an
earlier C. In progress answers unknown.

## GPA - app.domain.gpa (needs the E/R prefixes CoursePilot does not store).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.grades import (
    Outcome,
    OutcomeKind,
    Tri,
    earns_credit,
    is_c_or_better_final,
    meets_minimum,
    outcome,
)


def term_order(term_code: str | None) -> int | None:
    """Rutgers term codes YYYYT (T: 0 winter, 1 spring, 7 summer, 9 fall)
    sort chronologically as integers. Anything else has no known order."""
    if term_code and len(term_code) == 5 and term_code.isdigit() and term_code[4] in "0179":
        return int(term_code)
    return None


@dataclass(frozen=True, slots=True)
class Attempt:
    course_key: str
    term_code: str
    status: str
    grade: str | None
    credit_origin: str = "rutgers"
    #: Opaque handle for callers (the Degree Engine keeps its StudentCourse).
    handle: Any = field(default=None, compare=False, hash=False)

    @property
    def outcome(self) -> Outcome:
        return outcome(self.status, self.grade, self.credit_origin)

    def evidence(self) -> dict:
        return {"course": self.course_key, "term": self.term_code, "status": self.status,
                "grade": self.grade, "credit_origin": self.credit_origin}


# --------------------------------------------------------------------------
# degree domain
# --------------------------------------------------------------------------


@dataclass(slots=True)
class DegreeAttempts:
    eligible: list[Attempt]
    e_credit: list[Attempt]
    #: An earlier non-final/ungraded attempt makes it unknowable whether the
    #: attempts after it are E credit.
    uncertain_from: int | None = None   # index into `eligible`

    @property
    def has_uncertain_followers(self) -> bool:
        return self.uncertain_from is not None and self.uncertain_from < len(self.eligible) - 1

    def is_uncertain(self, attempt: Attempt) -> bool:
        return self.uncertain_from is not None and self.eligible.index(attempt) > self.uncertain_from


def _decisive(a: Attempt) -> Tri:
    """Does this attempt make every later attempt E credit?"""
    o = a.outcome
    if o.kind is OutcomeKind.CREDIT_WITHOUT_GRADE:
        return Tri.YES if a.credit_origin == "exam_credit" else Tri.UNKNOWN
    if o.kind in (OutcomeKind.IN_PROGRESS, OutcomeKind.NOT_COMPLETED):
        return Tri.NO
    return is_c_or_better_final(o)


def degree_attempts(attempts: list[Attempt]) -> DegreeAttempts:
    """Split one course's attempts into degree-eligible and E-credit ones."""
    ordered = sorted(attempts, key=lambda a: (term_order(a.term_code) is None,
                                              term_order(a.term_code) or 0, a.status))
    unordered = any(term_order(a.term_code) is None for a in attempts) and len(attempts) > 1
    out = DegreeAttempts(eligible=[], e_credit=[])
    decided = False
    for a in ordered:
        if a.status == "planned":
            continue
        if decided:
            out.e_credit.append(a)
            continue
        out.eligible.append(a)
        d = _decisive(a)
        if d is Tri.YES:
            decided = True
        elif d is Tri.UNKNOWN and out.uncertain_from is None:
            out.uncertain_from = len(out.eligible) - 1
    if unordered and len(out.eligible) > 1 and out.uncertain_from is None:
        out.uncertain_from = 0
    return out


@dataclass(slots=True)
class CreditChoice:
    attempt: Attempt | None
    provisional: bool                  # in progress / non-final / uncertain
    reason: str


def degree_credit_attempt(attempts: list[Attempt]) -> CreditChoice:
    """The ONE attempt that represents this course for degree credit."""
    split = degree_attempts(attempts)
    earned = [a for a in split.eligible if a.status == "completed"
              and earns_credit(a.outcome) is Tri.YES]
    if earned:
        chosen = earned[-1]
        return CreditChoice(chosen, split.is_uncertain(chosen),
                            "repeat_after_ungraded_attempt" if split.is_uncertain(chosen)
                            else "earned")
    pending = [a for a in split.eligible if earns_credit(a.outcome) is Tri.UNKNOWN]
    if pending:
        return CreditChoice(pending[-1], True, f"outcome_not_final:{pending[-1].outcome.kind}")
    return CreditChoice(None, False, "no_credit_earned")


@dataclass(slots=True)
class GradeCheck:
    """One minimum-grade decision with the evidence for it."""

    status: Tri
    required: str
    attempt: Attempt | None
    reason: str

    def evidence(self) -> dict:
        return {"required_grade": self.required, "result": self.status.value,
                "reason": self.reason,
                "attempt": self.attempt.evidence() if self.attempt else None}


def degree_minimum_grade(attempts: list[Attempt], minimum: str) -> GradeCheck:
    """Is "`minimum` or better" met for DEGREE purposes (SAS repeat policy)?"""
    split = degree_attempts(attempts)
    met = [a for a in split.eligible if meets_minimum(a.outcome, minimum) is Tri.YES]
    if met:
        chosen = met[-1]
        if split.is_uncertain(chosen):
            return GradeCheck(Tri.UNKNOWN, minimum, chosen, "repeat_after_ungraded_attempt")
        return GradeCheck(Tri.YES, minimum, chosen, "meets_minimum")
    unknown = [a for a in split.eligible if meets_minimum(a.outcome, minimum) is Tri.UNKNOWN]
    if unknown:
        a = unknown[-1]
        return GradeCheck(Tri.UNKNOWN, minimum, a, f"grade_not_comparable:{a.outcome.kind}")
    later = [a for a in split.e_credit if meets_minimum(a.outcome, minimum) is Tri.YES]
    if later:
        return GradeCheck(Tri.NO, minimum, later[-1], "qualifying_attempt_is_e_credit")
    last = split.eligible[-1] if split.eligible else None
    return GradeCheck(Tri.NO, minimum, last, "below_minimum" if last else "not_taken")


# --------------------------------------------------------------------------
# course-eligibility domain
# --------------------------------------------------------------------------


def prerequisite_grade(attempts: list[Attempt], minimum: str | None) -> GradeCheck:
    """Has the student EARNED this course (with `minimum` or better)?

    Any attempt counts; an E-credit repeat does not unearn an earlier grade.
    """
    if minimum is None:
        done = [a for a in attempts if a.status == "completed"
                and earns_credit(a.outcome) is Tri.YES]
        if done:
            return GradeCheck(Tri.YES, "", done[-1], "passed")
        open_ = [a for a in attempts if a.status != "planned"
                 and earns_credit(a.outcome) is Tri.UNKNOWN]
        if open_:
            return GradeCheck(Tri.UNKNOWN, "", open_[-1], f"pending:{open_[-1].outcome.kind}")
        return GradeCheck(Tri.NO, "", attempts[-1] if attempts else None,
                          "not_passed" if attempts else "not_taken")
    met = [a for a in attempts if meets_minimum(a.outcome, minimum) is Tri.YES]
    if met:
        return GradeCheck(Tri.YES, minimum, met[-1], "meets_minimum")
    unknown = [a for a in attempts if a.status != "planned"
               and meets_minimum(a.outcome, minimum) is Tri.UNKNOWN]
    if unknown:
        return GradeCheck(Tri.UNKNOWN, minimum, unknown[-1],
                          f"grade_not_comparable:{unknown[-1].outcome.kind}")
    return GradeCheck(Tri.NO, minimum, attempts[-1] if attempts else None,
                      "below_minimum" if attempts else "not_taken")


__all__ = ["Attempt", "CreditChoice", "DegreeAttempts", "GradeCheck", "degree_attempts",
           "degree_credit_attempt", "degree_minimum_grade", "prerequisite_grade", "term_order"]
