"""Fabricated progress trees: denominators, choices, uncertainty and projections."""

from decimal import Decimal
from fractions import Fraction

import pytest

from app.domain.audit import RequirementResult
from app.domain.audit import RequirementStatus as S
from app.services.progress import (
    Measure,
    _scope_measure,
    contribution,
    display_denominator,
    measure,
)


def node(code="R", kind="course", status=S.UNSATISFIED, **values):
    return RequirementResult(
        requirement_code=code,
        requirement_name=code,
        requirement_type=kind,
        status=status,
        reason="Fabricated",
        **values,
    )


def test_course_count_partial_and_credit_partial_keep_native_units():
    courses = measure(
        node(kind="choose_n", status=S.PARTIALLY_SATISFIED, needed_count=5, satisfied_count=2)
    )
    credits = measure(
        node(
            kind="credits",
            status=S.PARTIALLY_SATISFIED,
            needed_credits=Decimal(6),
            satisfied_credits=Decimal(3),
        )
    )
    assert courses.earned == Fraction(2, 5) and courses.budget == 1
    assert credits.earned == Fraction(1, 2) and credits.budget == 1


def test_parent_nodes_never_double_count_the_denominator():
    root = node(
        kind="all_of",
        children=[
            node("A", status=S.SATISFIED),
            node("G", kind="all_of", children=[node("B"), node("C")]),
        ],
    )
    value = measure(root)
    assert (value.earned, value.budget) == (1, 3)


def test_choice_group_denominator_excludes_unchosen_alternatives():
    children = [node("A", status=S.SATISFIED), node("B"), node("C")]
    value = measure(node(kind="any_of", needed_count=1, children=children))
    assert (value.earned, value.budget) == (1, 1)


@pytest.mark.parametrize("status", [S.INDETERMINATE, S.NOT_EVALUABLE])
def test_unknown_never_counts_complete_or_disappears_from_denominator(status):
    value = measure(node(status=status, needed_count=1, satisfied_count=1))
    assert (value.earned, value.budget, value.unknown) == (0, 1, 1)


def test_categories_and_credit_minimums_constrain_count_progress():
    value = measure(
        node(
            kind="choose_n",
            status=S.PARTIALLY_SATISFIED,
            needed_count=2,
            satisfied_count=2,
            needed_distinct_categories=2,
            distinct_categories=1,
            needed_credits=Decimal(6),
            satisfied_credits=Decimal(4),
        )
    )
    assert value.earned == Fraction(1, 2)


def test_full_count_with_engine_rejected_constraint_is_never_100_percent():
    value = measure(node(kind="choose_n", needed_count=2, satisfied_count=2))
    assert value.earned == 0


def test_empty_definition_is_unavailable_not_zero_or_complete():
    value = measure(node(kind="all_of", children=[]))
    assert value.budget == 0
    assert contribution(value.earned, value.budget).percent is None


def test_exact_rational_percentage_survives_display_rounding():
    value = contribution(Fraction(1), 3)
    assert value.percent == Decimal("33.33")
    assert value.exact_percent.numerator == 100
    assert value.exact_percent.denominator == 3


def test_scope_selection_is_explicit_never_inferred_from_code_prefix():
    roots = [node("misleading-core-name", status=S.SATISFIED), node("unrelated-name")]
    systems = {"misleading-core-name": "major", "unrelated-name": "core"}
    major = _scope_measure(roots, systems, {"major"})
    core = _scope_measure(roots, systems, {"core"})
    assert (major.earned, major.budget) == (1, 1)
    assert (core.earned, core.budget) == (0, 1)


def test_partial_in_progress_result_requires_separate_completed_evaluation():
    completed = node(
        kind="choose_n", needed_count=2, satisfied_count=1, status=S.PARTIALLY_SATISFIED
    )
    projected = node(
        kind="choose_n", needed_count=2, satisfied_count=2, status=S.PROVISIONALLY_SATISFIED
    )
    assert measure(completed).earned == Fraction(1, 2)
    assert measure(projected).earned == 1


def test_mixed_scope_choice_does_not_invent_independent_completion():
    choice = node(kind="any_of", needed_count=1, children=[node("A"), node("B")])
    value = _scope_measure([choice], {"A": "major", "B": "core"}, {"core"})
    assert value.earned == 0 and value.unknown == 1


def test_completed_attempt_with_provisional_campus_or_grade_is_not_confirmed():
    provisional = node(status=S.PROVISIONALLY_SATISFIED, needed_count=1, satisfied_count=1)
    confirmed = measure(provisional, confirmed_only=True)
    assert (confirmed.earned, confirmed.budget, confirmed.unknown) == (0, 1, 1)


def test_requirement_specific_unknown_blocks_numeric_partial_confirmation():
    partial = node(kind="choose_n", status=S.PARTIALLY_SATISFIED, needed_count=3, satisfied_count=2)
    known = measure(partial)
    uncertain = measure(partial, confirmed_only=True, uncertain_codes={"R"})
    assert known.earned == Fraction(2, 3)
    assert uncertain.earned == 0 and uncertain.unknown == 1


def test_empty_unknown_group_cannot_disappear_inside_known_overall_denominator():
    empty = node("MISSING", kind="all_of", status=S.INDETERMINATE, children=[])
    known = node("KNOWN", status=S.SATISFIED)
    result = measure(node(kind="all_of", children=[known, empty]))
    assert not result.budget_known and result.unknown == 1


def test_mixed_scope_multi_choice_budget_does_not_fall_back_to_one():
    choice = node(kind="any_of", needed_count=2, children=[node("A"), node("B"), node("C")])
    result = _scope_measure([choice], {"A": "major", "B": "core", "C": "core"}, {"core"})
    assert result.budget == 2 and not result.budget_known and result.unknown == 2


def test_unknown_budget_in_either_evaluation_withholds_the_denominator():
    known = Measure(Fraction(1), 3, 0, True)
    unknown = Measure(Fraction(1), 3, 1, False)
    assert display_denominator(known, known) == 3
    assert display_denominator(unknown, known) == 0
    assert display_denominator(known, unknown) == 0
