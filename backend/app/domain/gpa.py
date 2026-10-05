"""Grade-point average, computed only where Rutgers defines every input (Phase 6.4).

## Formula (SAS "Grades and Records", Cumulative Grade-Point Average)

  "Grade (numerical equivalent) x Credits = Grade Points";
  GPA = total grade points / total credit hours of the courses included.

## Scopes

| scope | courses | supported |
|---|---|---|
| `cumulative` | every Rutgers attempt that enters the GPA | yes |
| `subject:<code>` | attempts in one subject (e.g. "GPA in computer science courses") | yes |
| `courses:<k1>,<k2>,...` | an explicit course list | yes |
| `major` | "grade-point average ... in the major" | NO - Rutgers does not define which courses; never computed |

## Which attempts (app.domain.grades.gpa_points)

Included: Rutgers letter grades A..F. Excluded: P/NC, W, NG, H, S/U, TZ,
transfer and exam credit ("not computed in the cumulative grade-point
average"), in-progress and planned. Unknown: other T grades, XF and
unrecognized symbols - any of them makes the GPA UNKNOWN.

## Repeats (SAS "Repeating Courses")

  * After an attempt of C or better, later attempts are E credit: excluded.
  * After an F or D, "both the original grade and the new grade remain ...
    in the cumulative grade-point average" - EXCEPT that a student's
    elective repeat policy ("up to 16 credits in no more than four courses")
    E-prefixes the original. CoursePilot does not store prefixes, so a course
    with an F/D followed by another graded attempt makes the GPA UNKNOWN.

## Completeness

A GPA computed from a self-reported record covers only what was reported.
`evaluate_min_gpa` therefore answers SATISFIED/UNSATISFIED only when the
caller asserts the record is complete; otherwise UNKNOWN, with the computed
value kept as evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.domain.attempts import Attempt, degree_attempts
from app.domain.grades import OutcomeKind, Tri, gpa_points

UNSUPPORTED_SCOPES = {"major": "Rutgers does not define which courses a 'GPA in the major' covers"}


@dataclass(slots=True)
class GpaResult:
    status: Tri                  # YES = computed; UNKNOWN = cannot be computed
    scope: str
    value: Decimal | None
    grade_points: Decimal
    credits: Decimal
    included: list[dict] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    unknown_reasons: list[str] = field(default_factory=list)


def _in_scope(scope: str, attempt: Attempt, subject_of) -> bool | None:
    if scope == "cumulative":
        return True
    if scope.startswith("subject:"):
        return subject_of(attempt.course_key) == scope.split(":", 1)[1]
    if scope.startswith("courses:"):
        return attempt.course_key in set(scope.split(":", 1)[1].split(","))
    return None


def _subject(course_key: str) -> str | None:
    parts = course_key.split(":")
    return parts[1] if len(parts) == 3 else None


def compute_gpa(attempts: list[Attempt], credits_of: dict, scope: str = "cumulative",
                subject_of=_subject) -> GpaResult:
    """`credits_of[(course_key, term_code)]` gives each attempt's credits."""
    zero = Decimal(0)
    if scope in UNSUPPORTED_SCOPES:
        return GpaResult(Tri.UNKNOWN, scope, None, zero, zero,
                         unknown_reasons=[f"unsupported_scope:{scope}"])
    if not (scope == "cumulative" or scope.startswith(("subject:", "courses:"))):
        return GpaResult(Tri.UNKNOWN, scope, None, zero, zero,
                         unknown_reasons=[f"unknown_scope:{scope}"])
    out = GpaResult(Tri.YES, scope, None, zero, zero)
    by_course: dict[str, list[Attempt]] = {}
    for a in attempts:
        if _in_scope(scope, a, subject_of):
            by_course.setdefault(a.course_key, []).append(a)
    for course_key in sorted(by_course):
        split = degree_attempts(by_course[course_key])
        for a in split.e_credit:
            out.excluded.append({**a.evidence(), "why": "e_credit_repeat"})
        graded = [a for a in split.eligible
                  if gpa_points(a.outcome)[0] is Tri.YES]
        if len(graded) > 1 and any(a.outcome.letter in ("D", "F") for a in graded[:-1]):
            out.unknown_reasons.append(f"repeat_policy_prefix_unknown:{course_key}")
        if split.has_uncertain_followers:
            out.unknown_reasons.append(f"attempt_order_uncertain:{course_key}")
        for a in split.eligible:
            included, points = gpa_points(a.outcome)
            if included is Tri.UNKNOWN:
                out.unknown_reasons.append(f"gpa_effect_unknown:{course_key}:{a.grade}")
                continue
            if included is Tri.NO:
                why = ("not_rutgers_graded" if a.outcome.kind is OutcomeKind.CREDIT_WITHOUT_GRADE
                       else f"not_in_gpa:{a.outcome.kind}")
                out.excluded.append({**a.evidence(), "why": why})
                continue
            credits = credits_of.get((a.course_key, a.term_code))
            if credits is None:
                out.unknown_reasons.append(f"credits_unknown:{course_key}:{a.term_code}")
                continue
            out.grade_points += points * Decimal(credits)
            out.credits += Decimal(credits)
            out.included.append({**a.evidence(), "credits": str(credits),
                                 "points": str(points)})
    if out.unknown_reasons:
        out.status = Tri.UNKNOWN
    elif out.credits > 0:
        out.value = (out.grade_points / out.credits).quantize(Decimal("0.001"), ROUND_HALF_UP)
    else:
        out.status = Tri.UNKNOWN
        out.unknown_reasons.append("no_graded_credits")
    return out


@dataclass(slots=True)
class GpaRuleResult:
    status: Tri                  # YES satisfied, NO unsatisfied, UNKNOWN
    threshold: Decimal
    gpa: GpaResult
    reason: str


def evaluate_min_gpa(attempts: list[Attempt], credits_of: dict, threshold: Decimal,
                     scope: str, *, record_complete: bool = False) -> GpaRuleResult:
    """"GPA of at least `threshold` in `scope`" - inclusive boundary."""
    result = compute_gpa(attempts, credits_of, scope)
    if result.status is not Tri.YES:
        return GpaRuleResult(Tri.UNKNOWN, threshold, result,
                             "cannot compute: " + ", ".join(result.unknown_reasons))
    if not record_complete:
        return GpaRuleResult(Tri.UNKNOWN, threshold, result,
                             f"computed {result.value} from the recorded courses, but the "
                             "record is not known to be complete")
    ok = result.value >= threshold
    return GpaRuleResult(Tri.YES if ok else Tri.NO, threshold, result,
                         f"GPA {result.value} {'>=' if ok else '<'} {threshold}")


__all__ = ["UNSUPPORTED_SCOPES", "GpaResult", "GpaRuleResult", "compute_gpa", "evaluate_min_gpa"]
