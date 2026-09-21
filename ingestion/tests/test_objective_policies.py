"""Global-objective POLICY comparison (Phase 4.4 investigation).

INVESTIGATION artifact. No production behaviour changes in this phase, and
**no policy here is endorsed**. Every test states what a policy does, not what
CoursePilot ought to do.

The policies (see `app/services/audit/policies.py`):

    A  completion-first        (satisfied, progress, slots)
    B  progress-preserving     (-regressions, satisfied, progress, slots)
    C  completion + monotonic  (satisfied, -regressions, progress, slots)
    D  student-configurable    a selector over A and B

Scoring runs over allocations enumerated by the Phase 4.3 oracle, which knows
nothing about policies. The search is shared; the scoring is not.

## Synthetic data

Requirement DEFINITIONS are SYNTHETIC and are not Rutgers requirements.
Courses here are abstract keys - these tests exercise the policy mathematics,
not the ingestion path.
"""

from __future__ import annotations

from fractions import Fraction

import pytest
from app.services.audit.oracle import (
    OracleAllocation,
    OracleProblem,
    OracleRequirement,
    enumerate_optima,
    solve,
)
from app.services.audit.policies import (
    POLICIES,
    PolicyContext,
    compare_policies,
    evaluate_policy,
    policy_a_completion_first,
    policy_a_slots_only,
    policy_b_progress_preserving,
    policy_c_completion_with_monotonicity,
    policy_d_configurable,
    progress_fraction,
    progress_slots,
    regressions,
)


def _req(code, need, courses, *, system="core", distinct=0, order=0, cats=None):
    categories = {c: frozenset() for c in courses}
    if cats:
        categories = {c: frozenset(v) for c, v in cats.items()}
    return OracleRequirement(
        code=code,
        system=system,
        needed_count=need,
        needed_categories=distinct,
        categories=categories,
        sort_key=(order, code),
    )


def _problem(reqs, courses, *, share=False):
    return OracleProblem(tuple(reqs), tuple(sorted(courses)), share_across_systems=share)


def _ctx(*codes):
    return PolicyContext(baseline_satisfied=frozenset(codes))


# ==========================================================================
# Case A - one completion vs one preserved completion
# ==========================================================================


def test_case_a_single_course_wanted_by_two_requirements() -> None:
    """c1 can complete R_KEEP (already satisfied) or R_NEW. Not both.

        keep  : R_KEEP satisfied, R_NEW unsatisfied   1 satisfied, 0 regressions
        switch: R_NEW satisfied, R_KEEP unsatisfied   1 satisfied, 1 regression

    Completions TIE, so the policies split on the tie-break alone:
      A is indifferent (it never looks at the baseline)
      B and C both keep, because preservation is free here.
    """
    problem = _problem(
        [_req("R_KEEP", 1, ["c1"]), _req("R_NEW", 1, ["c1"], order=1)], ["c1"]
    )
    context = _ctx("R_KEEP")

    outcomes = compare_policies(problem, context)
    for outcome in outcomes.values():
        assert len(outcome.satisfied) == 1

    assert outcomes["B_progress_preserving"].satisfied == {"R_KEEP"}
    assert outcomes["C_completion_monotonic"].satisfied == {"R_KEEP"}
    assert outcomes["B_progress_preserving"].regressed == frozenset()
    assert outcomes["C_completion_monotonic"].regressed == frozenset()


# ==========================================================================
# Case B - one completion vs two completions (no conflict)
# ==========================================================================


def test_case_b_two_completions_beat_one_for_every_policy() -> None:
    """Nothing is given up, so every policy agrees. The uncontroversial case."""
    problem = _problem(
        [
            _req("R1", 1, ["c1"]),
            _req("R2", 1, ["c2"], order=1),
        ],
        ["c1", "c2"],
    )
    outcomes = compare_policies(problem, _ctx("R1"))

    for outcome in outcomes.values():
        assert outcome.satisfied == {"R1", "R2"}
        assert outcome.regressed == frozenset()


# ==========================================================================
# Case C - completing MORE requires a regression  (the discriminator)
# ==========================================================================

# R_HELD holds two courses and is satisfied. Releasing them completes TWO
# other requirements. This is the shape where preservation genuinely costs a
# completion - and note it requires the held requirement to occupy at least
# two courses (see test_single_course_release_can_never_gain, below).
_CONFLICT = [
    _req("R_HELD", 2, ["c1", "c2"]),
    _req("R_ONE", 1, ["c1"], order=1),
    _req("R_TWO", 1, ["c2"], order=2),
]


def test_case_c_policies_diverge_when_completion_costs_a_completion() -> None:
    """The case the product decision is actually about.

        keep    : R_HELD satisfied              1 satisfied, 0 regressions
        release : R_ONE + R_TWO satisfied       2 satisfied, 1 regression

    A and C take the extra completion. B declines it to avoid undoing one.
    Stated without preference: these policies value different things.
    """
    problem = _problem(_CONFLICT, ["c1", "c2"])
    context = _ctx("R_HELD")
    outcomes = compare_policies(problem, context)

    a = outcomes["A_completion_first"]
    b = outcomes["B_progress_preserving"]
    c = outcomes["C_completion_monotonic"]

    assert a.satisfied == {"R_ONE", "R_TWO"}
    assert a.regressed == {"R_HELD"}

    assert b.satisfied == {"R_HELD"}
    assert b.regressed == frozenset()

    # C behaves like A here: the regression is NECESSARY for the maximum.
    assert c.satisfied == {"R_ONE", "R_TWO"}
    assert c.regressed == {"R_HELD"}


def test_case_c_policy_b_trades_a_completion_for_preservation() -> None:
    """Quantifies exactly what Policy B gives up: one completion."""
    problem = _problem(_CONFLICT, ["c1", "c2"])
    context = _ctx("R_HELD")

    a = evaluate_policy(problem, "A_completion_first", context)
    b = evaluate_policy(problem, "B_progress_preserving", context)

    assert len(a.satisfied) - len(b.satisfied) == 1
    assert len(a.regressed) - len(b.regressed) == 1


# ==========================================================================
# Case D - partial progress that enables no completion
# ==========================================================================


def test_case_d_partial_progress_is_kept_when_it_costs_nothing() -> None:
    """R_BIG needs 3 and only 2 courses exist, so it can never complete.

    Nothing competes for those courses, so every policy parks them there and
    reports 2 of 3. Partial progress is preserved because it is free - not
    because any policy values it.
    """
    problem = _problem([_req("R_BIG", 3, ["c1", "c2"])], ["c1", "c2"])
    outcomes = compare_policies(problem, _ctx())

    for outcome in outcomes.values():
        assert outcome.satisfied == frozenset()
        assert outcome.allocation.filled_slots == 2


def test_case_d_progress_measure_changes_the_answer() -> None:
    """Whether 'more progress' means SLOTS or FRACTIONS is a real choice.

    One allocation puts both courses in a 4-course requirement; the other
    splits them across two 2-course requirements. Same slots, different
    fractions - so the two progress measures disagree, and neither is more
    correct without a product statement.
    """
    problem = _problem(
        [
            _req("R_WIDE", 4, ["c1", "c2"]),
            _req("R_N1", 2, ["c1"], order=1),
            _req("R_N2", 2, ["c2"], order=2),
        ],
        ["c1", "c2"],
    )
    both_wide = OracleAllocation((("c1", "R_WIDE", ""), ("c2", "R_WIDE", "")))
    split = OracleAllocation((("c1", "R_N1", ""), ("c2", "R_N2", "")))

    assert both_wide.is_valid(problem) and split.is_valid(problem)
    # Identical on the weight-free measure...
    assert progress_slots(both_wide, problem) == progress_slots(split, problem) == 2
    # ...and different on the fraction measure, which favours the split.
    assert progress_fraction(split, problem) == Fraction(1)
    assert progress_fraction(both_wide, problem) == Fraction(1, 2)
    assert progress_fraction(split, problem) > progress_fraction(both_wide, problem)


# ==========================================================================
# Case E - dead-end requirement
# ==========================================================================


def test_case_e_dead_end_courses_are_redirected_by_every_policy() -> None:
    """R_DEAD needs 2 and holds the only course that could complete R_LIVE.

    No policy defends a requirement that cannot finish, because none of them
    scores unreachable progress above a completion.
    """
    problem = _problem(
        [_req("R_DEAD", 2, ["c1"]), _req("R_LIVE", 1, ["c1"], order=1)], ["c1"]
    )
    outcomes = compare_policies(problem, _ctx())

    for name, outcome in outcomes.items():
        assert outcome.satisfied == {"R_LIVE"}, name


def test_case_e_dead_end_is_defended_by_b_only_if_it_was_satisfied() -> None:
    """A dead end cannot have been satisfied, so B never defends one.

    Worth pinning: Policy B preserves COMPLETIONS, not occupancy. A
    requirement holding courses it can never finish has nothing to preserve.
    """
    problem = _problem(
        [_req("R_DEAD", 2, ["c1"]), _req("R_LIVE", 1, ["c1"], order=1)], ["c1"]
    )
    outcome = evaluate_policy(problem, "B_progress_preserving", _ctx("R_DEAD"))

    # The baseline claims R_DEAD was satisfied, which is impossible here;
    # B still cannot restore it, so it takes the achievable completion.
    assert outcome.satisfied == {"R_LIVE"}


# ==========================================================================
# Case F - category semantics are a CONSTRAINT, not a preference
# ==========================================================================


def test_case_f_no_policy_can_fake_a_category() -> None:
    """2 courses / 2 distinct categories, with both courses certified Xp.

    Every policy must report it unsatisfied. Phase 4.2 semantics bind the
    search space; a policy only ranks what is already legal.
    """
    problem = _problem(
        [_req("AH", 2, ["c1", "c2"], distinct=2, cats={"c1": ["Xp"], "c2": ["Xp"]})],
        ["c1", "c2"],
    )
    outcomes = compare_policies(problem, _ctx("AH"))

    for name, outcome in outcomes.items():
        assert outcome.satisfied == frozenset(), name
        assert outcome.allocation.filled_slots == 2, name


def test_case_f_multi_category_course_counts_once_under_every_policy() -> None:
    problem = _problem(
        [_req("AH", 2, ["c1"], distinct=2, cats={"c1": ["Xp", "Xq"]})], ["c1"]
    )
    for name in POLICIES:
        outcome = evaluate_policy(problem, name, _ctx())
        assert outcome.allocation.filled_slots == 1, name
        assert outcome.satisfied == frozenset(), name


def test_case_f_category_requirement_can_still_be_completed() -> None:
    problem = _problem(
        [_req("AH", 2, ["c1", "c2"], distinct=2, cats={"c1": ["Xp"], "c2": ["Xo"]})],
        ["c1", "c2"],
    )
    for name in POLICIES:
        assert evaluate_policy(problem, name, _ctx()).satisfied == {"AH"}, name


# ==========================================================================
# Case G - multi-system sharing is untouched by policy
# ==========================================================================


def test_case_g_sharing_rules_hold_under_every_policy() -> None:
    """One course serves one slot per system when sharing is on..."""
    problem = _problem(
        [
            _req("MAJ", 1, ["c1"], system="major"),
            _req("CORE", 1, ["c1"], system="core", order=1),
        ],
        ["c1"],
        share=True,
    )
    for name in POLICIES:
        outcome = evaluate_policy(problem, name, _ctx())
        assert outcome.satisfied == {"MAJ", "CORE"}, name


def test_case_g_exclusive_still_allows_only_one() -> None:
    """...and exactly one when it is off. No policy may loosen this."""
    problem = _problem(
        [
            _req("MAJ", 1, ["c1"], system="major"),
            _req("CORE", 1, ["c1"], system="core", order=1),
        ],
        ["c1"],
        share=False,
    )
    for name in POLICIES:
        outcome = evaluate_policy(problem, name, _ctx("CORE"))
        assert len(outcome.satisfied) == 1, name


# ==========================================================================
# Case H - ties and deterministic tie-breaking
# ==========================================================================


def test_case_h_ties_are_resolved_identically_on_repeat_runs() -> None:
    """Tie-breaking must be technical and stable, never an academic claim.

    Two symmetric requirements, one course: the policies cannot distinguish
    them, so the canonical ordering decides - and decides the same way every
    time.
    """
    problem = _problem(
        [_req("R_A", 1, ["c1"]), _req("R_B", 1, ["c1"], order=1)], ["c1"]
    )
    context = _ctx()

    for name in POLICIES:
        runs = [evaluate_policy(problem, name, context).allocation for _ in range(5)]
        assert all(r.triples == runs[0].triples for r in runs), name


def test_case_h_tied_optima_are_genuinely_multiple() -> None:
    """The tie is real, not an artifact of the search order."""
    problem = _problem(
        [_req("R_A", 1, ["c1"]), _req("R_B", 1, ["c1"], order=1)], ["c1"]
    )
    score, optima = enumerate_optima(
        problem, lambda a, p: policy_a_completion_first(a, p, _ctx())
    )
    satisfied_sets = {tuple(sorted(a.satisfied(problem))) for a in optima}
    assert satisfied_sets == {("R_A",), ("R_B",)}


# ==========================================================================
# Section 10 - is Policy C actually different from A?
# ==========================================================================


def test_policy_c_never_completes_fewer_requirements_than_a() -> None:
    """C's first key IS A's first key, so C can only refine A's ties.

    Checked across a family of instances rather than asserted.
    """
    instances = [
        (_CONFLICT, ["c1", "c2"], ("R_HELD",)),
        ([_req("R_KEEP", 1, ["c1"]), _req("R_NEW", 1, ["c1"], order=1)], ["c1"], ("R_KEEP",)),
        (
            [
                _req("R_HELD", 2, ["c1", "c2"]),
                _req("R_ONE", 1, ["c1"], order=1),
            ],
            ["c1", "c2"],
            ("R_HELD",),
        ),
        ([_req("R_BIG", 3, ["c1", "c2"]), _req("R_S", 1, ["c1"], order=1)], ["c1", "c2"], ()),
    ]
    for reqs, courses, baseline in instances:
        problem = _problem(reqs, courses)
        context = _ctx(*baseline)
        a = evaluate_policy(problem, "A_completion_first", context)
        c = evaluate_policy(problem, "C_completion_monotonic", context)
        assert len(c.satisfied) == len(a.satisfied), (reqs, a.satisfied, c.satisfied)
        assert len(c.regressed) <= len(a.regressed)


def test_policy_c_differs_from_a_only_on_ties() -> None:
    """Where C and A disagree, they agree on the completion count.

    R_KEEP and R_NEW are interchangeable to A; C breaks the tie toward the
    baseline. That is the entire practical content of Policy C.
    """
    problem = _problem(
        [_req("R_NEW", 1, ["c1"]), _req("R_KEEP", 1, ["c1"], order=1)], ["c1"]
    )
    context = _ctx("R_KEEP")

    a = evaluate_policy(problem, "A_completion_first", context)
    c = evaluate_policy(problem, "C_completion_monotonic", context)

    assert len(a.satisfied) == len(c.satisfied) == 1
    assert c.satisfied == {"R_KEEP"}
    assert c.regressed == frozenset()
    # A had no reason to prefer either.
    assert a.satisfied in ({"R_KEEP"}, {"R_NEW"})


def test_policy_c_does_not_guarantee_monotonicity() -> None:
    """The finding that matters about Policy C.

    Its name suggests a guarantee it does not provide: when the maximum
    completion count REQUIRES undoing a satisfied requirement, C undoes it,
    exactly like A. C buys preservation only where preservation is free.
    """
    problem = _problem(_CONFLICT, ["c1", "c2"])
    outcome = evaluate_policy(problem, "C_completion_monotonic", _ctx("R_HELD"))

    assert outcome.regressed == {"R_HELD"}


# ==========================================================================
# Section 6 - monotonicity under course ADDITION
# ==========================================================================


def test_monotonicity_adding_courses_can_undo_a_completion_under_a_and_c() -> None:
    """The concrete monotonicity violation.

    BEFORE the student holds c1 and c2, and R_HELD is the only thing they can
    complete. AFTER adding c3 and c4, releasing c1 and c2 completes R_ONE and
    R_TWO - two completions for one regression.

    Under A and C, a student sees R_HELD flip from satisfied to unsatisfied
    after taking courses that have nothing to do with it. Under B it does not.
    """
    reqs = [
        _req("R_HELD", 2, ["c1", "c2"]),
        _req("R_ONE", 2, ["c1", "c3"], order=1),
        _req("R_TWO", 2, ["c2", "c4"], order=2),
    ]
    before = _problem(reqs, ["c1", "c2"])
    baseline = solve(before, lambda a, p: policy_a_completion_first(a, p, _ctx()))
    baseline_satisfied = baseline.satisfied(before)
    assert baseline_satisfied == {"R_HELD"}

    after = _problem(reqs, ["c1", "c2", "c3", "c4"])
    context = PolicyContext(baseline_satisfied=baseline_satisfied)
    outcomes = compare_policies(after, context)

    assert outcomes["A_completion_first"].satisfied == {"R_ONE", "R_TWO"}
    assert outcomes["A_completion_first"].regressed == {"R_HELD"}
    assert outcomes["C_completion_monotonic"].regressed == {"R_HELD"}
    # Policy B keeps R_HELD and completes fewer requirements.
    assert outcomes["B_progress_preserving"].regressed == frozenset()
    assert "R_HELD" in outcomes["B_progress_preserving"].satisfied


def test_single_course_release_can_never_gain_a_completion() -> None:
    """Why regressions need a MULTI-course requirement to be worth it.

    A satisfied requirement holding ONE course can free at most one course,
    which can complete at most one other requirement - so releasing it trades
    one completion for one. The result is always a tie, never a strict gain,
    and A therefore never has a reason to regress such a requirement.

    This bounds how often monotonicity can be violated at all.
    """
    problem = _problem(
        [
            _req("R_HELD", 1, ["c1"]),
            _req("R_OTHER", 1, ["c1"], order=1),
        ],
        ["c1"],
    )
    context = _ctx("R_HELD")
    score, optima = enumerate_optima(
        problem, lambda a, p: policy_a_completion_first(a, p, context)
    )
    # Every optimum completes exactly one requirement.
    for allocation in optima:
        assert len(allocation.satisfied(problem)) == 1


def test_empty_baseline_collapses_b_and_c_onto_a() -> None:
    """With no history there is nothing to preserve, so all three coincide.

    Important for the architecture question: a first-ever audit has no
    baseline, so B and C only start to differ once CoursePilot remembers
    something.
    """
    problem = _problem(_CONFLICT, ["c1", "c2"])
    outcomes = compare_policies(problem, _ctx())

    counts = {name: len(o.satisfied) for name, o in outcomes.items()}
    assert len(set(counts.values())) == 1, counts
    assert all(o.regressed == frozenset() for o in outcomes.values())


# ==========================================================================
# Policy D - logical viability only
# ==========================================================================


def test_policy_d_is_a_selector_not_a_new_objective() -> None:
    problem = _problem(_CONFLICT, ["c1", "c2"])
    context = _ctx("R_HELD")

    completion = policy_d_configurable("completion")
    preservation = policy_d_configurable("preservation")

    assert completion is policy_a_completion_first
    assert preservation is policy_b_progress_preserving

    best_completion = solve(problem, lambda a, p: completion(a, p, context))
    best_preservation = solve(problem, lambda a, p: preservation(a, p, context))
    assert best_completion.satisfied(problem) != best_preservation.satisfied(problem)


def test_policy_d_unknown_preference_is_rejected() -> None:
    """An unknown preference must fail loudly rather than pick a default."""
    with pytest.raises(ValueError, match="unknown preference"):
        policy_d_configurable("whatever")


def test_policy_d_same_transcript_two_students_two_audits() -> None:
    """The consequence worth surfacing before any UI exists.

    Identical transcripts produce different audits when the preference
    differs - so the preference becomes part of what an audit MEANS, and a
    student toggling it sees requirements change state without taking a
    course.
    """
    problem = _problem(_CONFLICT, ["c1", "c2"])
    context = _ctx("R_HELD")

    a = solve(problem, lambda x, p: policy_d_configurable("completion")(x, p, context))
    b = solve(problem, lambda x, p: policy_d_configurable("preservation")(x, p, context))

    assert len(a.satisfied(problem)) == 2
    assert len(b.satisfied(problem)) == 1
    assert regressions(a, problem, context) == {"R_HELD"}
    assert regressions(b, problem, context) == frozenset()


# ==========================================================================
# Section 5 - satisfaction state versus progress
# ==========================================================================


def test_fraction_progress_prefers_spreading_over_concentrating() -> None:
    """The "is 2/3 better than 1/1?" warning, made concrete - and the answer
    is not the obvious one.

        complete_small : R_DONE 1/1 + R_PART 2/3   -> fraction 5/3
        feed_big       : R_PART 3/3                -> fraction 1

    Both complete exactly ONE requirement and fill three slots, so they tie
    on satisfaction and on slots. The fraction measure then prefers
    complete_small, because finishing a 1-course requirement and advancing a
    3-course one sums to more than finishing the 3-course one alone.

    That is a genuine preference for SPREADING progress, and nobody stated
    it. It falls out of summing fractions with different denominators - which
    is precisely why "maximize partial progress" cannot be adopted without
    saying which measure is meant.
    """
    problem = _problem(
        [
            _req("R_DONE", 1, ["c1"]),
            _req("R_PART", 3, ["c1", "c2", "c3"], order=1),
        ],
        ["c1", "c2", "c3"],
    )
    complete_small = OracleAllocation(
        (("c1", "R_DONE", ""), ("c2", "R_PART", ""), ("c3", "R_PART", ""))
    )
    feed_big = OracleAllocation(
        (("c1", "R_PART", ""), ("c2", "R_PART", ""), ("c3", "R_PART", ""))
    )
    assert complete_small.is_valid(problem) and feed_big.is_valid(problem)

    context = _ctx()
    # Tied on completions AND on slots.
    assert len(feed_big.satisfied(problem)) == 1
    assert len(complete_small.satisfied(problem)) == 1
    assert progress_slots(feed_big, problem) == progress_slots(complete_small, problem)

    # The fraction measure breaks the tie toward the spread allocation.
    assert progress_fraction(complete_small, problem) == Fraction(5, 3)
    assert progress_fraction(feed_big, problem) == Fraction(1)
    assert policy_a_completion_first(
        complete_small, problem, context
    ) > policy_a_completion_first(feed_big, problem, context)

    # The weight-free measure cannot tell them apart at all.
    assert policy_a_slots_only(complete_small, problem, context) == policy_a_slots_only(
        feed_big, problem, context
    )


def test_progress_measures_can_rank_allocations_differently() -> None:
    """Direct evidence that 'maximize partial progress' is under-specified."""
    problem = _problem(
        [
            _req("R_WIDE", 5, ["c1", "c2"]),
            _req("R_SMALL", 2, ["c1"], order=1),
        ],
        ["c1", "c2"],
    )
    wide = OracleAllocation((("c1", "R_WIDE", ""), ("c2", "R_WIDE", "")))
    split = OracleAllocation((("c1", "R_SMALL", ""), ("c2", "R_WIDE", "")))
    assert wide.is_valid(problem) and split.is_valid(problem)

    context = _ctx()
    assert progress_slots(wide, problem) == progress_slots(split, problem)
    assert progress_fraction(split, problem) > progress_fraction(wide, problem)
    # The slots-only variant of Policy A cannot tell them apart...
    assert policy_a_slots_only(wide, problem, context) == policy_a_slots_only(
        split, problem, context
    )
    # ...while the fraction variant prefers the split.
    assert policy_a_completion_first(
        split, problem, context
    ) > policy_a_completion_first(wide, problem, context)


# ==========================================================================
# No arbitrary weights anywhere
# ==========================================================================


def test_no_policy_uses_a_numeric_requirement_weight() -> None:
    """Guard against a weight creeping in later.

    Every policy score is a tuple of counts and exact fractions derived from
    the requirement definitions. Swapping two requirements' codes and orders
    must not change the SHAPE of the outcome, because nothing ranks a
    requirement by identity.
    """
    forward = _problem(
        [_req("ALPHA", 1, ["c1"]), _req("BETA", 1, ["c2"], order=1)], ["c1", "c2"]
    )
    reverse = _problem(
        [_req("BETA", 1, ["c2"]), _req("ALPHA", 1, ["c1"], order=1)], ["c1", "c2"]
    )
    for name in POLICIES:
        a = evaluate_policy(forward, name, _ctx())
        b = evaluate_policy(reverse, name, _ctx())
        assert a.satisfied == b.satisfied == {"ALPHA", "BETA"}, name


def test_policy_scores_are_exact_not_floating_point() -> None:
    """Fractions, so a tie is a tie rather than a rounding accident."""
    problem = _problem([_req("R", 3, ["c1"])], ["c1"])
    score = policy_a_completion_first(solve(problem), problem, _ctx())
    assert isinstance(score[1], Fraction)
    assert score[1] == Fraction(1, 3)
