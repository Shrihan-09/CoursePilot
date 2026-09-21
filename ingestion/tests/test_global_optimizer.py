"""Decomposed global optimizer, verified against the oracle (Phase 4.5).

The optimizer (`app.services.audit.optimizer`) shares no code with the Phase
4.3 oracle (`app.services.audit.oracle`) - not its types, not its
decomposition, not its search. Both are built here from the same requirement
definition and compared on SCORE, which is what makes the agreement evidence
rather than a tautology.

The optimizer IS the production allocation path as of Phase 4.5, under the
adopted objective C. The oracle is never in that path.

## Synthetic data

Requirement DEFINITIONS are SYNTHETIC and are not Rutgers requirements.
"""

from __future__ import annotations

import pytest
from app.services.audit import oracle as O
from app.services.audit.optimizer import (
    OBJECTIVE_A,
    OBJECTIVE_B,
    OBJECTIVE_C,
    OBJECTIVE_SLOTS,
    AllocationProblem,
    Assignment,
    CandidateAllocation,
    ObjectiveContext,
    OptimizerBoundExceeded,
    RequirementSpec,
    decompose,
    optimize,
)

ALL_OBJECTIVES = (OBJECTIVE_A, OBJECTIVE_B, OBJECTIVE_C)


def _spec(code, need, courses, *, system="core", distinct=0, order=0, cats=None):
    categories = {c: frozenset() for c in courses}
    if cats:
        categories = {c: frozenset(v) for c, v in cats.items()}
    return RequirementSpec(
        code=code,
        system=system,
        needed_count=need,
        needed_categories=distinct,
        categories=categories,
        sort_key=(order, code),
    )


def _problem(specs, courses, *, share=False):
    return AllocationProblem(tuple(specs), tuple(sorted(courses)), share_across_systems=share)


def _as_oracle(problem: AllocationProblem) -> O.OracleProblem:
    """The SAME instance, rebuilt for the independent oracle."""
    return O.OracleProblem(
        requirements=tuple(
            O.OracleRequirement(
                code=r.code,
                system=r.system,
                needed_count=r.needed_count,
                needed_categories=r.needed_categories,
                categories=dict(r.categories),
                sort_key=r.sort_key,
            )
            for r in problem.requirements
        ),
        course_keys=problem.course_keys,
        share_across_systems=problem.share_across_systems,
    )


def _ctx(*codes):
    return ObjectiveContext(baseline_satisfied=frozenset(codes))


# ==========================================================================
# optimizer vs independent oracle
# ==========================================================================

_CASES = {
    "dead_end": ([_spec("R_BIG", 2, ["c1"]), _spec("R_ONE", 1, ["c1"], order=1)], ["c1"]),
    "competing": (
        [_spec("R_POOL", 1, ["c1", "c2"]), _spec("R_SCARCE", 1, ["c1"], order=1)],
        ["c1", "c2"],
    ),
    "threshold_two": (
        [
            _spec("R1", 2, ["c1", "c2"]),
            _spec("R2", 1, ["c1"], order=1),
            _spec("R3", 1, ["c2"], order=2),
        ],
        ["c1", "c2"],
    ),
    "threshold_one_only": (
        [_spec("R1", 1, ["c1"]), _spec("R2", 1, ["c2"], order=1)],
        ["c1", "c2"],
    ),
    "category": (
        [_spec("AH", 2, ["c1", "c2"], distinct=2, cats={"c1": ["Xp"], "c2": ["Xo"]})],
        ["c1", "c2"],
    ),
    "category_impossible": (
        [_spec("AH", 2, ["c1", "c2"], distinct=2, cats={"c1": ["Xp"], "c2": ["Xp"]})],
        ["c1", "c2"],
    ),
    "multi_category": (
        [_spec("AH", 2, ["c1", "c2"], distinct=2, cats={"c1": ["Xp", "Xq"], "c2": ["Xp"]})],
        ["c1", "c2"],
    ),
    "held_release": (
        [
            _spec("R_HELD", 2, ["c1", "c2"]),
            _spec("R_ONE", 1, ["c1"], order=1),
            _spec("R_TWO", 1, ["c2"], order=2),
        ],
        ["c1", "c2"],
    ),
    "disconnected": (
        [
            _spec("A1", 1, ["c1"]),
            _spec("A2", 1, ["c1"], order=1),
            _spec("B1", 1, ["c3"], order=2),
        ],
        ["c1", "c3"],
    ),
}


@pytest.mark.parametrize("case", sorted(_CASES))
@pytest.mark.parametrize("objective", ALL_OBJECTIVES, ids=lambda o: o.name)
def test_optimizer_matches_the_independent_oracle(case, objective) -> None:
    """The core correctness check: same optimal SCORE as exhaustive search.

    Compared on score, not on the exact allocation - several allocations can
    attain the optimum and picking a different one is not a disagreement.
    """
    specs, courses = _CASES[case]
    problem = _problem(specs, courses)
    context = _ctx()

    result = optimize(problem, objective, context)
    assert result.exact

    oracle_problem = _as_oracle(problem)
    oracle_best = O.solve(
        oracle_problem,
        lambda a, p: _oracle_score(objective, a, p, context, problem),
    )

    ours = objective(result.allocation, problem, context)
    theirs = _oracle_score(objective, oracle_best, oracle_problem, context, problem)
    assert ours == theirs, (case, objective.name, ours, theirs)


def _oracle_score(objective, oracle_allocation, oracle_problem, context, problem):
    """Score an ORACLE allocation with the optimizer's objective.

    Converts the allocation across the two independent representations so the
    comparison is of objectives, not of data structures.
    """
    converted = CandidateAllocation(
        tuple(
            Assignment(course, code, category)
            for course, code, category in oracle_allocation.triples
        )
    )
    return objective(converted, problem, context)


@pytest.mark.parametrize("case", sorted(_CASES))
def test_optimizer_matches_oracle_on_satisfied_sets(case) -> None:
    """Completion COUNT must match the oracle for the completion-first
    objective, which is the quantity the whole phase is about."""
    specs, courses = _CASES[case]
    problem = _problem(specs, courses)
    oracle_problem = _as_oracle(problem)

    ours = optimize(problem, OBJECTIVE_A, _ctx())
    theirs = O.solve(oracle_problem, O.max_satisfied_objective)

    assert len(ours.allocation.satisfied(problem)) == len(
        theirs.satisfied(oracle_problem)
    ), case


# ==========================================================================
# the defect Phase 4.3 measured
# ==========================================================================


def test_dead_end_requirement_is_redirected() -> None:
    """The Case C2 defect: today's allocator spends the course on a
    requirement that can never finish. Completion-first does not."""
    problem = _problem(
        [_spec("R_BIG", 2, ["c1"]), _spec("R_ONE", 1, ["c1"], order=1)], ["c1"]
    )
    result = optimize(problem, OBJECTIVE_A, _ctx())

    assert result.allocation.satisfied(problem) == {"R_ONE"}
    # And the slots-only objective is indifferent - it fills one slot either way.
    slots = optimize(problem, OBJECTIVE_SLOTS, _ctx())
    assert slots.allocation.filled_slots == 1


def test_one_completion_beats_two_partials() -> None:
    problem = _problem(
        [_spec("A", 2, ["c1", "c2"]), _spec("B", 2, ["c1", "c2"], order=1)], ["c1", "c2"]
    )
    result = optimize(problem, OBJECTIVE_A, _ctx())
    assert len(result.allocation.satisfied(problem)) == 1
    assert result.allocation.filled_slots == 2


def test_threshold_one_instances_match_maximum_matching() -> None:
    """Phase 4.3 proved that all-threshold-1 reduces to maximum-cardinality
    matching, which the existing allocator already computes optimally."""
    problem = _problem(
        [
            _spec("R1", 1, ["c1", "c2"]),
            _spec("R2", 1, ["c1"], order=1),
            _spec("R3", 1, ["c2"], order=2),
        ],
        ["c1", "c2"],
    )
    result = optimize(problem, OBJECTIVE_A, _ctx())
    assert len(result.allocation.satisfied(problem)) == 2


# ==========================================================================
# Phase 4.2 compatibility - category semantics preserved exactly
# ==========================================================================


def test_category_distinctness_is_satisfaction_not_validity() -> None:
    """THE Phase 4.2 invariant. Two courses may both take Xp - that is a
    LEGAL allocation which evaluates to 1 of 2 categories. The optimizer must
    generate it rather than prune it as illegal."""
    problem = _problem(
        [_spec("AH", 2, ["c1", "c2"], distinct=2, cats={"c1": ["Xp"], "c2": ["Xp"]})],
        ["c1", "c2"],
    )
    result = optimize(problem, OBJECTIVE_A, _ctx())

    assert result.allocation.filled_slots == 2          # allocation is valid
    assert result.allocation.satisfied(problem) == frozenset()   # and unsatisfying
    assert len(set(result.allocation.categories_for("AH"))) == 1


def test_case_d_category_aware_pair_is_chosen() -> None:
    """Phase 4.2 Case D: an Xp+Xo pair exists and must be found."""
    problem = _problem(
        [
            _spec(
                "AH",
                2,
                ["c1", "c2", "c3"],
                distinct=2,
                cats={"c1": ["Xp"], "c2": ["Xp"], "c3": ["Xo"]},
            )
        ],
        ["c1", "c2", "c3"],
    )
    result = optimize(problem, OBJECTIVE_A, _ctx())

    assert result.allocation.satisfied(problem) == {"AH"}
    assert len(set(result.allocation.categories_for("AH"))) == 2


def test_case_f_one_multi_category_course_covers_one_category() -> None:
    """Phase 4.2 Case F, verified from Rutgers wording: one course occupies
    one category position."""
    problem = _problem(
        [_spec("AH", 2, ["c1"], distinct=2, cats={"c1": ["Xp", "Xq"]})], ["c1"]
    )
    result = optimize(problem, OBJECTIVE_A, _ctx())

    assert result.allocation.filled_slots == 1
    assert len(set(result.allocation.categories_for("AH"))) == 1
    assert result.allocation.satisfied(problem) == frozenset()


def test_slot_category_records_the_selected_edge() -> None:
    """Each assignment carries the category actually chosen, never the union
    of what the course could have counted as."""
    problem = _problem(
        [_spec("AH", 2, ["c1", "c2"], distinct=2, cats={"c1": ["Xp", "Xq"], "c2": ["Xp"]})],
        ["c1", "c2"],
    )
    result = optimize(problem, OBJECTIVE_A, _ctx())

    by_course = {a.course_key: a.category for a in result.allocation.assignments}
    assert by_course["c1"] in {"Xp", "Xq"}
    assert by_course["c2"] == "Xp"
    assert len(set(by_course.values())) == 2      # augmenting path found


def test_no_course_is_used_twice_within_a_requirement() -> None:
    problem = _problem(
        [_spec("AH", 2, ["c1"], distinct=2, cats={"c1": ["Xp", "Xq"]})], ["c1"]
    )
    result = optimize(problem, OBJECTIVE_A, _ctx())
    courses = [a.course_key for a in result.allocation.assignments]
    assert len(courses) == len(set(courses))


# ==========================================================================
# sharing policy
# ==========================================================================


def test_sharing_allows_one_slot_per_system() -> None:
    problem = _problem(
        [
            _spec("MAJ", 1, ["c1"], system="major"),
            _spec("CORE", 1, ["c1"], system="core", order=1),
        ],
        ["c1"],
        share=True,
    )
    result = optimize(problem, OBJECTIVE_A, _ctx())
    assert result.allocation.satisfied(problem) == {"MAJ", "CORE"}


def test_exclusive_allows_only_one() -> None:
    problem = _problem(
        [
            _spec("MAJ", 1, ["c1"], system="major"),
            _spec("CORE", 1, ["c1"], system="core", order=1),
        ],
        ["c1"],
        share=False,
    )
    result = optimize(problem, OBJECTIVE_A, _ctx())
    assert len(result.allocation.satisfied(problem)) == 1


# ==========================================================================
# decomposition
# ==========================================================================


def test_components_are_independent() -> None:
    problem = _problem(
        [
            _spec("A1", 1, ["c1"]),
            _spec("A2", 1, ["c1"], order=1),
            _spec("B1", 1, ["c3"], order=2),
            _spec("B2", 1, ["c4"], order=3),
        ],
        ["c1", "c3", "c4"],
    )
    parts = decompose(problem)
    codes = sorted(tuple(sorted(r.code for r in p.requirements)) for p in parts)
    assert codes == [("A1", "A2"), ("B1",), ("B2",)]


def test_single_component_is_not_split() -> None:
    problem = _problem(
        [_spec("R1", 1, ["c1", "c2"]), _spec("R2", 1, ["c2"], order=1)], ["c1", "c2"]
    )
    assert len(decompose(problem)) == 1


def test_decomposed_result_equals_whole_instance_optimum() -> None:
    """Soundness of decomposition, checked against the oracle on the WHOLE
    instance rather than per component."""
    specs = [
        _spec("A1", 1, ["c1"]),
        _spec("A2", 1, ["c1"], order=1),
        _spec("B1", 2, ["c3", "c4"], order=2),
    ]
    problem = _problem(specs, ["c1", "c3", "c4"])

    ours = optimize(problem, OBJECTIVE_A, _ctx())
    assert ours.components == 2

    oracle_problem = _as_oracle(problem)
    theirs = O.solve(oracle_problem, O.max_satisfied_objective)
    assert len(ours.allocation.satisfied(problem)) == len(
        theirs.satisfied(oracle_problem)
    )


def test_empty_problem_is_handled() -> None:
    problem = _problem([], [])
    result = optimize(problem, OBJECTIVE_A, _ctx())
    assert result.allocation.assignments == ()
    assert result.exact


# ==========================================================================
# bounds and fallback - never a silent wrong answer
# ==========================================================================


def test_bound_exceeded_is_reported_not_hidden() -> None:
    """The optimizer must never present an unproven result as optimal."""
    specs = [_spec(f"R{i}", 2, [f"c{j}" for j in range(8)], order=i) for i in range(5)]
    problem = _problem(specs, [f"c{j}" for j in range(8)])

    result = optimize(problem, OBJECTIVE_A, _ctx(), bound=50)

    assert result.exact is False
    assert result.fallback_components
    with pytest.raises(OptimizerBoundExceeded):
        result.require_exact()


def test_exact_result_passes_require_exact() -> None:
    problem = _problem([_spec("R1", 1, ["c1"])], ["c1"])
    result = optimize(problem, OBJECTIVE_A, _ctx())
    assert result.exact
    assert result.require_exact() is result.allocation


def test_states_explored_is_reported() -> None:
    problem = _problem(
        [_spec("R1", 1, ["c1", "c2"]), _spec("R2", 1, ["c2"], order=1)], ["c1", "c2"]
    )
    result = optimize(problem, OBJECTIVE_A, _ctx())
    assert result.states_explored > 0
    assert result.components == 1


# ==========================================================================
# determinism
# ==========================================================================


@pytest.mark.parametrize("objective", ALL_OBJECTIVES, ids=lambda o: o.name)
def test_optimizer_is_deterministic(objective) -> None:
    problem = _problem(
        [
            _spec("R1", 2, ["c1", "c2"]),
            _spec("R2", 1, ["c1"], order=1),
            _spec("R3", 1, ["c2"], order=2),
        ],
        ["c1", "c2"],
    )
    runs = [optimize(problem, objective, _ctx()).allocation for _ in range(5)]
    assert all(r.assignments == runs[0].assignments for r in runs)


# ==========================================================================
# objectives behave as declared
# ==========================================================================


def test_objective_b_declines_a_completion_to_avoid_a_regression() -> None:
    problem = _problem(
        [
            _spec("R_HELD", 2, ["c1", "c2"]),
            _spec("R_ONE", 1, ["c1"], order=1),
            _spec("R_TWO", 1, ["c2"], order=2),
        ],
        ["c1", "c2"],
    )
    context = _ctx("R_HELD")

    a = optimize(problem, OBJECTIVE_A, context).allocation
    b = optimize(problem, OBJECTIVE_B, context).allocation

    assert a.satisfied(problem) == {"R_ONE", "R_TWO"}
    assert b.satisfied(problem) == {"R_HELD"}


def test_objective_c_matches_a_on_completions_always() -> None:
    """C's first key is A's first key, so it can only refine ties."""
    for baseline in ((), ("R_HELD",), ("R_ONE",)):
        problem = _problem(
            [
                _spec("R_HELD", 2, ["c1", "c2"]),
                _spec("R_ONE", 1, ["c1"], order=1),
                _spec("R_TWO", 1, ["c2"], order=2),
            ],
            ["c1", "c2"],
        )
        context = _ctx(*baseline)
        a = optimize(problem, OBJECTIVE_A, context).allocation
        c = optimize(problem, OBJECTIVE_C, context).allocation
        assert len(c.satisfied(problem)) == len(a.satisfied(problem))


def test_empty_baseline_makes_all_objectives_agree_on_completions() -> None:
    problem = _problem(
        [
            _spec("R_HELD", 2, ["c1", "c2"]),
            _spec("R_ONE", 1, ["c1"], order=1),
            _spec("R_TWO", 1, ["c2"], order=2),
        ],
        ["c1", "c2"],
    )
    counts = {
        o.name: len(optimize(problem, o, _ctx()).allocation.satisfied(problem))
        for o in ALL_OBJECTIVES
    }
    assert len(set(counts.values())) == 1, counts


def test_adopted_objective_is_c_without_a_progress_term() -> None:
    """The product decision, pinned.

    Policy C: completions first, then regressions avoided where free, then
    filled slots. The partial-progress component was removed deliberately -
    summed fractions embed an unstated weighting and were the measured
    performance bottleneck.
    """
    from app.services.audit import optimizer

    assert optimizer.DEFAULT_OBJECTIVE is optimizer.OBJECTIVE_C

    problem = _problem([_spec("R", 3, ["c1"])], ["c1"])
    score = optimizer.OBJECTIVE_C(
        optimize(problem, optimizer.OBJECTIVE_C, _ctx()).allocation, problem, _ctx()
    )
    # (satisfied, -regressions, slots) - three components, all integers.
    assert len(score) == 3
    assert all(isinstance(x, int) for x in score)


def test_engine_uses_the_optimizer_but_never_the_oracle() -> None:
    """The optimizer is production; the oracle must never be.

    An oracle in the production path would make the verification circular -
    and it is exponential besides.
    """
    import inspect

    from app.services.audit import engine

    source = inspect.getsource(engine)
    assert "optimizer" in source
    assert "oracle" not in source
