"""Grade semantics, attempts and GPA (Phase 6.4) - pure, no database.

Every expectation traces to the archived SAS catalog text quoted in
app/domain/grades.py, attempts.py and gpa.py. Where the text stops, the
expected answer is UNKNOWN.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.attempts import (
    Attempt,
    degree_attempts,
    degree_credit_attempt,
    degree_minimum_grade,
    prerequisite_grade,
    term_order,
)
from app.domain.gpa import compute_gpa, evaluate_min_gpa
from app.domain.grades import (
    LETTERS,
    OutcomeKind,
    Tri,
    at_most,
    earns_credit,
    gpa_points,
    meets_minimum,
    outcome,
)

Y, N, U = Tri.YES, Tri.NO, Tri.UNKNOWN
K = "01:198:112"


def done(grade, term="20259", origin="rutgers", key=K):
    return Attempt(key, term, "completed", grade, origin)


def ip(term="20269", key=K):
    return Attempt(key, term, "in_progress", None)


# ==========================================================================
# grade primitives
# ==========================================================================


def test_scale_is_the_published_rutgers_scale() -> None:
    assert LETTERS == ("A", "B+", "B", "C+", "C", "D", "F")
    assert gpa_points(outcome("completed", "B+"))[1] == Decimal("3.5")
    assert gpa_points(outcome("completed", "D"))[1] == Decimal("1.0")


@pytest.mark.parametrize(("grade", "minimum", "expected"), [
    ("A", "C", Y), ("B+", "C", Y), ("C+", "C", Y),
    ("C", "C", Y),                                       # exact threshold
    ("D", "C", N), ("F", "C", N),                        # below threshold
    ("B", "B", Y), ("C+", "B", N), ("D", "D", Y), ("F", "D", N),
    ("P", "C", Y), ("P", "B", U),                        # Pass = A..C
    ("NC", "C", N), ("NC", "D", U),                      # No Credit = D or F
    ("W", "C", N), ("XF", "D", N), ("U", "C", N),
    ("TB", "C", U), ("TZ", "C", U), ("NG", "C", U), ("H", "C", U), ("S", "C", U),
    ("D-", "C", U), ("A+", "C", U), ("??", "C", U),       # unrecognized: never guessed
])
def test_meets_minimum(grade, minimum, expected) -> None:
    assert meets_minimum(outcome("completed", grade), minimum) is expected


def test_case_and_whitespace_do_not_change_meaning() -> None:
    assert meets_minimum(outcome("completed", " b+ "), "C") is Y


def test_in_progress_and_planned() -> None:
    assert meets_minimum(outcome("in_progress", None), "C") is U
    assert earns_credit(outcome("in_progress", None)) is U
    assert earns_credit(outcome("planned", None)) is N


def test_credit_without_a_rutgers_grade() -> None:
    for origin in ("transfer", "exam_credit"):
        o = outcome("completed", None, origin)
        assert o.kind is OutcomeKind.CREDIT_WITHOUT_GRADE
        assert earns_credit(o) is Y                   # establishes completion
        assert meets_minimum(o, "C") is U             # no comparable letter grade
        assert gpa_points(o) == (N, None)             # "not computed in the ... GPA"
    # A symbol recorded with transfer credit is not a Rutgers grade.
    assert meets_minimum(outcome("completed", "A", "transfer"), "C") is U


@pytest.mark.parametrize(("grade", "credit"), [
    ("A", Y), ("D", Y), ("F", N), ("P", Y), ("NC", N), ("W", N), ("XF", N), ("S", N),
    ("U", N), ("H", Y), ("TC", U), ("NG", U), ("ZZ", U)])
def test_earns_credit(grade, credit) -> None:
    assert earns_credit(outcome("completed", grade)) is credit


@pytest.mark.parametrize(("grade", "limit", "expected"), [
    ("D", "D", Y), ("C", "D", N), ("F", "D", Y), ("P", "D", N), ("P", "C", U), ("P", "A", Y),
    ("NC", "D", Y), ("NC", "F", U), ("XF", "F", Y), ("TC", "D", U)])
def test_at_most(grade, limit, expected) -> None:
    assert at_most(outcome("completed", grade), limit) is expected


def test_invalid_minimum_is_refused() -> None:
    for bad in ("F", "E", "", "C-"):
        with pytest.raises(ValueError):
            meets_minimum(outcome("completed", "A"), bad)


def test_term_order() -> None:
    assert term_order("20261") < term_order("20267") < term_order("20269") < term_order("20270")
    assert term_order("2026-fall") is None and term_order("20263") is None


# ==========================================================================
# retakes - degree domain (SAS repeated-course policy)
# ==========================================================================


def test_fail_then_pass() -> None:
    attempts = [done("F", "20259"), done("B", "20261")]
    assert degree_credit_attempt(attempts).attempt.grade == "B"
    assert degree_minimum_grade(attempts, "C").status is Y


def test_pass_then_lower_retake_is_e_credit() -> None:
    """C then D: the D is E credit; the C still counts and still meets C."""
    attempts = [done("C", "20259"), done("D", "20261")]
    split = degree_attempts(attempts)
    assert [a.grade for a in split.eligible] == ["C"] and [a.grade for a in split.e_credit] == ["D"]
    assert degree_credit_attempt(attempts).attempt.grade == "C"
    assert degree_minimum_grade(attempts, "C").status is Y


def test_qualifying_then_higher_retake_does_not_raise_the_degree_grade() -> None:
    """C then B: the B is E credit, so "B or better" is NOT met for the degree."""
    check = degree_minimum_grade([done("C", "20259"), done("B", "20261")], "B")
    assert check.status is N and check.reason == "qualifying_attempt_is_e_credit"


def test_low_then_qualifying() -> None:
    attempts = [done("D", "20259"), done("B", "20261")]
    assert degree_minimum_grade(attempts, "C").status is Y
    assert degree_credit_attempt(attempts).attempt.grade == "B"


def test_d_counts_for_credit_but_not_for_a_c_minimum() -> None:
    attempts = [done("D", "20259")]
    choice = degree_credit_attempt(attempts)
    assert choice.attempt.grade == "D" and not choice.provisional
    check = degree_minimum_grade(attempts, "C")
    assert (check.status, check.reason) == (N, "below_minimum")


def test_in_progress_retake() -> None:
    attempts = [done("D", "20259"), ip("20269")]
    assert degree_credit_attempt(attempts).attempt.grade == "D"    # earned beats pending
    assert degree_minimum_grade(attempts, "C").status is U          # the retake may qualify
    assert degree_minimum_grade([done("F", "20259"), ip("20269")], "C").status is U


def test_unknown_outcome_before_a_retake_is_uncertain() -> None:
    attempts = [done("TC", "20259"), done("B", "20261")]
    check = degree_minimum_grade(attempts, "C")
    assert (check.status, check.reason) == (U, "repeat_after_ungraded_attempt")


def test_exam_credit_then_rutgers_enrollment_is_e_credit() -> None:
    attempts = [done(None, "20259", "exam_credit"), done("A", "20261")]
    split = degree_attempts(attempts)
    assert [a.credit_origin for a in split.eligible] == ["exam_credit"]
    # The later A is E credit; the exam credit has no comparable grade.
    check = degree_minimum_grade(attempts, "C")
    assert (check.status, check.reason) == (U, "grade_not_comparable:credit_without_grade")


# ==========================================================================
# retakes - prerequisite domain
# ==========================================================================


def test_prerequisite_any_qualifying_attempt_counts() -> None:
    assert prerequisite_grade([done("C", "20259"), done("D", "20261")], "C").status is Y
    assert prerequisite_grade([done("C", "20259"), done("B", "20261")], "B").status is Y
    assert prerequisite_grade([done("D", "20259")], "C").status is N
    assert prerequisite_grade([done("D", "20259"), ip()], "C").status is U
    assert prerequisite_grade([], "C").reason == "not_taken"
    assert prerequisite_grade([done(None, "20259", "transfer")], None).status is Y
    assert prerequisite_grade([done(None, "20259", "transfer")], "C").status is U


# ==========================================================================
# GPA
# ==========================================================================


def _credits(*attempts, value="3"):
    return {(a.course_key, a.term_code): Decimal(value) for a in attempts}


def test_gpa_weighted_by_credits() -> None:
    a = done("A", "20259", key="01:198:111")
    b = done("C", "20259", key="01:640:151")
    credits = {(a.course_key, a.term_code): Decimal("4"), (b.course_key, b.term_code): Decimal("2")}
    result = compute_gpa([a, b], credits)
    assert result.value == Decimal("3.333")           # (16 + 4) / 6


def test_gpa_excludes_non_letter_and_external_credit() -> None:
    a = done("B", "20259", key="01:198:111")
    p = done("P", "20259", key="01:198:112")
    t = done(None, "20259", "transfer", key="01:640:151")
    w = done("W", "20259", key="01:640:152")
    result = compute_gpa([a, p, t, w], _credits(a, p, t, w))
    assert result.value == Decimal("3.000") and len(result.included) == 1
    assert {e["why"] for e in result.excluded} == {"not_in_gpa:pass", "not_rutgers_graded",
                                                   "not_in_gpa:withdrawn"}


def test_gpa_repeat_after_d_is_unknown_without_prefixes() -> None:
    attempts = [done("D", "20259"), done("B", "20261")]
    result = compute_gpa(attempts, _credits(*attempts))
    assert result.status is U and result.value is None
    assert result.unknown_reasons == [f"repeat_policy_prefix_unknown:{K}"]


def test_gpa_repeat_after_c_excludes_the_e_credit_attempt() -> None:
    attempts = [done("C", "20259"), done("A", "20261")]
    result = compute_gpa(attempts, _credits(*attempts))
    assert result.value == Decimal("2.000")
    assert result.excluded[0]["why"] == "e_credit_repeat"


def test_gpa_unknown_inputs() -> None:
    t = done("TB", "20259")
    assert compute_gpa([t], _credits(t)).status is U
    a = done("A", "20259")
    assert compute_gpa([a], {}).unknown_reasons == [f"credits_unknown:{K}:20259"]
    assert compute_gpa([ip()], {}).unknown_reasons == ["no_graded_credits"]


def test_gpa_scopes() -> None:
    cs = done("A", "20259", key="01:198:111")
    ma = done("C", "20259", key="01:640:151")
    credits = _credits(cs, ma)
    assert compute_gpa([cs, ma], credits, "subject:198").value == Decimal("4.000")
    assert compute_gpa([cs, ma], credits, "courses:01:640:151").value == Decimal("2.000")
    major = compute_gpa([cs, ma], credits, "major")
    assert major.status is U and major.unknown_reasons[0].startswith("unsupported_scope:major")


def test_gpa_threshold_boundary_and_completeness() -> None:
    a = done("C", "20259", key="01:198:111")
    credits = _credits(a)
    at = evaluate_min_gpa([a], credits, Decimal("2.0"), "cumulative", record_complete=True)
    assert at.status is Y                                       # 2.000 >= 2.0 (inclusive)
    above = evaluate_min_gpa([a], credits, Decimal("2.001"), "cumulative", record_complete=True)
    assert above.status is N
    partial = evaluate_min_gpa([a], credits, Decimal("2.0"), "cumulative")
    assert partial.status is U and "not known to be complete" in partial.reason
    assert partial.gpa.value == Decimal("2.000")                # evidence kept
