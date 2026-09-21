"""Candidate global-objective policies (Phase 4.4 investigation).

NOT A PRODUCTION PATH, and no policy here is endorsed. This module exists so
that the alternatives can be scored against identical allocations and their
consequences measured rather than argued about.

Nothing in the audit engine imports this. It sits beside `oracle.py`, which
enumerates allocations; these functions only SCORE them.

## The conflict being modelled

An allocation that completes more requirements can take courses away from a
requirement that was previously complete. Phase 4.3 measured this on real
data: of 8 suboptimal transcripts, 5 bought a completion by removing progress
or an entire completion elsewhere.

    Allocation A            Allocation B
    CORE_SCL  satisfied     CORE_SCL  unsatisfied
    CORE_HST  partial       CORE_HST  satisfied
    CORE_QFR  partial       CORE_QFR  satisfied

B completes more. A keeps what the student already had. Neither is "better"
without a statement of what CoursePilot values, and this module does not make
that statement.

## Stateless versus stateful policies

A policy that talks about "preserving" or "regressing" needs a reference
point: satisfied compared to WHAT? That makes it **stateful** in a way
completion-first is not.

    Policy A   stateless   depends only on the current transcript
    Policy B   stateful    needs a baseline of previously satisfied requirements
    Policy C   stateful    same
    Policy D   either      whichever the student selected

This is an architectural consequence, not a detail: today every audit is
recomputed from scratch and CoursePilot stores no prior audit. Adopting B or
C means either persisting prior results or defining the baseline as "the
audit before the newest course", which are different products.

`PolicyContext.baseline_satisfied` is that reference point, and an empty
baseline makes B and C collapse onto A - itself a useful property to test.

## Why "maximize partial progress" is not weight-free

Section 5 of the Phase 4.4 brief warns against assuming 2/3 beats 1/1. The
warning generalises: ANY scalar progress measure across requirements with
different denominators embeds a weighting.

    filled slots      counts courses; every course counts the same
    sum of fractions  a slot in a 2-course requirement is worth 1/2, one in a
                      5-course requirement 1/5 - a weighting nobody stated

Both are implemented, deliberately, because they disagree. Choosing between
them is a product decision of exactly the kind this phase refuses to make
silently.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from fractions import Fraction

from .oracle import OracleAllocation, OracleProblem


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """The reference point a preservation policy is measured against.

    `baseline_satisfied` is the set of requirement codes that were satisfied
    BEFORE whatever change prompted this audit. An empty baseline means "no
    history", under which every policy here reduces to completion-first.
    """

    baseline_satisfied: frozenset[str] = frozenset()


def satisfied_count(allocation: OracleAllocation, problem: OracleProblem) -> int:
    return len(allocation.satisfied(problem))


def regressions(
    allocation: OracleAllocation, problem: OracleProblem, context: PolicyContext
) -> frozenset[str]:
    """Requirements that WERE satisfied and are not under this allocation.

    The quantity a student would experience as "it said I was done with this".
    """
    return frozenset(context.baseline_satisfied - allocation.satisfied(problem))


def preserved_count(
    allocation: OracleAllocation, problem: OracleProblem, context: PolicyContext
) -> int:
    return len(context.baseline_satisfied & allocation.satisfied(problem))


def progress_slots(allocation: OracleAllocation, problem: OracleProblem) -> int:
    """Progress as a count of filled slots.

    Weight-free: one allocated course counts as one, wherever it lands. It
    cannot distinguish "2 of 3" from "2 of 5".
    """
    return allocation.filled_slots


def progress_fraction(
    allocation: OracleAllocation, problem: OracleProblem
) -> Fraction:
    """Progress as the sum of per-requirement completion fractions.

    NOT weight-free, and included to show that. A slot in a 2-course
    requirement contributes 1/2 while a slot in a 5-course requirement
    contributes 1/5, so this silently ranks small requirements above large
    ones. Exact `Fraction` arithmetic, so the comparison never turns on
    floating-point noise.
    """
    total = Fraction(0)
    for req in problem.requirements:
        if req.needed_count <= 0:
            continue
        held = min(len(allocation.courses_for(req.code)), req.needed_count)
        total += Fraction(held, req.needed_count)
    return total


# --------------------------------------------------------------------------
# the policies
# --------------------------------------------------------------------------

Policy = Callable[[OracleAllocation, OracleProblem, PolicyContext], tuple]


def policy_a_completion_first(
    allocation: OracleAllocation, problem: OracleProblem, context: PolicyContext
) -> tuple:
    """POLICY A - completion-first.

        1. maximize fully satisfied requirements
        2. maximize partial progress
        3. maximize filled slots

    Stateless: it never looks at the baseline, so a previously satisfied
    requirement has no special standing and may be dismantled to complete two
    others.
    """
    return (
        satisfied_count(allocation, problem),
        progress_fraction(allocation, problem),
        progress_slots(allocation, problem),
    )


def policy_b_progress_preserving(
    allocation: OracleAllocation, problem: OracleProblem, context: PolicyContext
) -> tuple:
    """POLICY B - progress-preserving.

        1. MINIMIZE regressions          (never undo a completion)
        2. maximize fully satisfied requirements
        3. maximize partial progress
        4. maximize filled slots

    The ordering is the normative claim, and it is the one to argue about:
    putting regressions first means B will decline a strictly larger number of
    completions rather than undo one. With an empty baseline it is identical
    to A.
    """
    return (
        -len(regressions(allocation, problem, context)),
        satisfied_count(allocation, problem),
        progress_fraction(allocation, problem),
        progress_slots(allocation, problem),
    )


def policy_c_completion_with_monotonicity(
    allocation: OracleAllocation, problem: OracleProblem, context: PolicyContext
) -> tuple:
    """POLICY C - completion first, regressions broken as a tie.

        1. maximize fully satisfied requirements
        2. MINIMIZE regressions
        3. maximize partial progress
        4. maximize filled slots

    This is the formal reading of "never undo a completion unless necessary":
    necessity means no allocation achieves the maximum completion count
    without it. Because level 1 is identical to A's level 1, C can only ever
    differ from A among allocations that are ALREADY TIED on completions.

    That yields a provable relationship, checked in the tests:

        satisfied(C) == satisfied(A)   always
        regressions(C) <= regressions(A)

    So C is a strict refinement of A - it buys preservation only where
    preservation is free. It does NOT guarantee monotonicity.
    """
    return (
        satisfied_count(allocation, problem),
        -len(regressions(allocation, problem, context)),
        progress_fraction(allocation, problem),
        progress_slots(allocation, problem),
    )


def policy_d_configurable(preference: str) -> Policy:
    """POLICY D - whichever of A or B the student selected.

    Logically viable and mathematically uninteresting: it is a selector over
    the other policies, not a new objective. What it adds is not mathematics
    but consequences - two students with identical transcripts can see
    different audits, and a student toggling the preference sees requirements
    change state without taking a course.

    No UI is designed here; this exists to confirm the concept is coherent.
    """
    if preference == "completion":
        return policy_a_completion_first
    if preference == "preservation":
        return policy_b_progress_preserving
    raise ValueError(f"unknown preference {preference!r}")


#: Slot-progress variants, to show that the progress measure itself matters.
def policy_a_slots_only(
    allocation: OracleAllocation, problem: OracleProblem, context: PolicyContext
) -> tuple:
    """Policy A with weight-free progress instead of summed fractions."""
    return (
        satisfied_count(allocation, problem),
        progress_slots(allocation, problem),
    )


POLICIES: dict[str, Policy] = {
    "A_completion_first": policy_a_completion_first,
    "B_progress_preserving": policy_b_progress_preserving,
    "C_completion_monotonic": policy_c_completion_with_monotonicity,
}


@dataclass(frozen=True, slots=True)
class PolicyOutcome:
    """What one policy did to one instance."""

    policy: str
    allocation: OracleAllocation
    satisfied: frozenset[str]
    regressed: frozenset[str]
    score: tuple = field(default=())

    def summary(self) -> str:
        return (
            f"{self.policy}: satisfied={len(self.satisfied)} "
            f"regressed={len(self.regressed)}"
        )


def evaluate_policy(
    problem: OracleProblem,
    policy_name: str,
    context: PolicyContext,
    *,
    max_states: int | None = None,
) -> PolicyOutcome:
    """Optimal allocation under one policy, via exhaustive enumeration.

    Deliberately routed through the Phase 4.3 oracle, which knows nothing
    about policies: the search is shared, the scoring is not.
    """
    from .oracle import MAX_ORACLE_STATES, solve

    policy = POLICIES[policy_name]
    best = solve(
        problem,
        lambda a, p: policy(a, p, context),
        max_states=max_states or MAX_ORACLE_STATES,
    )
    return PolicyOutcome(
        policy=policy_name,
        allocation=best,
        satisfied=best.satisfied(problem),
        regressed=regressions(best, problem, context),
        score=policy(best, problem, context),
    )


def compare_policies(
    problem: OracleProblem,
    context: PolicyContext,
    *,
    max_states: int | None = None,
) -> dict[str, PolicyOutcome]:
    return {
        name: evaluate_policy(problem, name, context, max_states=max_states)
        for name in POLICIES
    }


__all__ = [
    "POLICIES",
    "Policy",
    "PolicyContext",
    "PolicyOutcome",
    "compare_policies",
    "evaluate_policy",
    "policy_a_completion_first",
    "policy_a_slots_only",
    "policy_b_progress_preserving",
    "policy_c_completion_with_monotonicity",
    "policy_d_configurable",
    "preserved_count",
    "progress_fraction",
    "progress_slots",
    "regressions",
    "satisfied_count",
]
