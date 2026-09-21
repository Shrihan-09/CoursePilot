"""Decomposed global allocation optimizer (Phase 4.5).

Currently **not wired into the audit engine**. It exists so the objective
chosen in Phase 4.4 can be implemented, verified and benchmarked before any
production allocation semantics change.

## Independence from the oracle

`oracle.py` is the Phase 4.3 exhaustive reference. This module shares no code
with it - not its problem types, not its decomposition, not its search. That
duplication is deliberate: an optimizer verified against an oracle it imports
proves only that the code agrees with itself. Tests build both from the same
requirement definition and compare scores.

## Architecture

    candidate allocation problem
            |
            v
    decompose into independent components        (this module)
            |
            v
    exhaustive search per component, with bounds (this module)
            |
            v
    combine component allocations
            |
            v
    requirement evaluation                       (engine.py - NOT here)

The optimizer answers "which allocation should be evaluated". It does not
decide what an allocation MEANS academically; that stays in the evaluator.
Nothing here duplicates requirement semantics beyond the minimum needed to
score a candidate.

## Category semantics are preserved exactly

Phase 4.2 established that category distinctness is a **satisfaction**
condition, not an allocation-validity constraint. So

    requirement: 2 courses, 2 distinct categories
    allocation : course A -> Xp, course B -> Xp

is a VALID allocation that evaluates to 1 of 2 categories. The search must
generate it, and must not prune it as illegal.

## Exactness is never silently traded away

Every result carries `exact`. When a component exceeds its state bound the
optimizer does not guess and does not quietly return its best-so-far as if it
were optimal: it marks the result inexact and records which components fell
back. A caller that requires optimality can refuse to use an inexact result.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from fractions import Fraction

#: Per-component state bound. Phase 4.3 measured a realistic 19-course
#: transcript decomposing into components of at most 8 courses, which is far
#: below this. The bound exists for the curriculum that does not decompose.
DEFAULT_COMPONENT_BOUND = 200_000


class OptimizerBoundExceeded(RuntimeError):
    """Raised only when the caller demanded an exact result and none was found."""


# --------------------------------------------------------------------------
# problem representation
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RequirementSpec:
    """One slot-consuming requirement, as the optimizer needs to see it.

    `categories` maps an eligible course key to the categories certifying it
    for THIS requirement. An empty frozenset means eligible but certified
    under no sub-category, which is every major requirement.
    """

    code: str
    system: str
    needed_count: int
    needed_categories: int = 0
    categories: Mapping[str, frozenset[str]] = field(default_factory=dict)
    sort_key: tuple = ()

    @property
    def eligible(self) -> frozenset[str]:
        return frozenset(self.categories)


@dataclass(frozen=True, slots=True)
class AllocationProblem:
    """One student's allocation instance."""

    requirements: tuple[RequirementSpec, ...]
    course_keys: tuple[str, ...]
    share_across_systems: bool = False
    #: course_key -> a STABLE NATURAL ordering key (course string, term...).
    #: Course keys are built from surrogate ids, so ordering on them would be
    #: deterministic within one database and arbitrary across a re-ingest -
    #: the same trap documented in DATA_MODEL.md 16.3. Callers pass natural
    #: keys so the chosen allocation survives reloading the catalog.
    course_order: Mapping[str, tuple] = field(default_factory=dict)

    def order_of(self, course_key: str) -> tuple:
        return self.course_order.get(course_key, (course_key,))

    def system_of(self, code: str) -> str:
        if not self.share_across_systems:
            return "*"
        for req in self.requirements:
            if req.code == code:
                return req.system
        return "*"


@dataclass(frozen=True, slots=True)
class Assignment:
    """(course, requirement, category) - the category is the EDGE selected."""

    course_key: str
    requirement_code: str
    category: str = ""


@dataclass(frozen=True, slots=True)
class CandidateAllocation:
    assignments: tuple[Assignment, ...] = ()

    @property
    def filled_slots(self) -> int:
        return len(self.assignments)

    def courses_for(self, code: str) -> tuple[str, ...]:
        return tuple(a.course_key for a in self.assignments if a.requirement_code == code)

    def categories_for(self, code: str) -> tuple[str, ...]:
        return tuple(
            a.category
            for a in self.assignments
            if a.requirement_code == code and a.category
        )

    def satisfied(self, problem: AllocationProblem) -> frozenset[str]:
        """Which requirements this allocation completes.

        Mirrors the evaluator's THRESHOLD rule for slot-consuming
        requirements: enough courses AND enough distinct categories. It does
        not attempt grades, exclusions, credits or group propagation - those
        stay in the evaluator, which sees the allocation afterwards.
        """
        done = set()
        for req in problem.requirements:
            if req.needed_count <= 0:
                continue
            if len(self.courses_for(req.code)) < req.needed_count:
                continue
            if req.needed_categories and (
                len(set(self.categories_for(req.code))) < req.needed_categories
            ):
                continue
            done.add(req.code)
        return frozenset(done)

    def merge(self, other: CandidateAllocation) -> CandidateAllocation:
        return CandidateAllocation(
            tuple(sorted(self.assignments + other.assignments, key=_assignment_key))
        )


def _assignment_key(a: Assignment) -> tuple:
    return (a.course_key, a.requirement_code, a.category)


def _natural_key(allocation: CandidateAllocation, order) -> tuple:
    """Canonical comparison key built from NATURAL course identities."""
    return tuple(
        (order(a.course_key), a.requirement_code, a.category)
        for a in allocation.assignments
    )


# --------------------------------------------------------------------------
# objectives
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObjectiveContext:
    """Reference state a preservation-aware objective is measured against.

    `baseline_satisfied` is what the student had ALREADY EARNED - see
    DATA_MODEL.md section 21. Empty means no baseline, under which every
    objective below reduces to completion-first.
    """

    baseline_satisfied: frozenset[str] = frozenset()


ObjectiveFn = Callable[[CandidateAllocation, AllocationProblem, ObjectiveContext], tuple]


@dataclass(frozen=True, slots=True)
class GlobalAllocationObjective:
    """A NAMED lexicographic objective.

    The tuple ordering is the whole normative content, so it is a declared
    object with a documented meaning rather than a comparison buried in a
    sort key. Every component is a count or an exact Fraction derived from
    requirement definitions; no requirement is weighted by identity.
    """

    name: str
    description: str
    score: ObjectiveFn

    def __call__(
        self,
        allocation: CandidateAllocation,
        problem: AllocationProblem,
        context: ObjectiveContext,
    ) -> tuple:
        return self.score(allocation, problem, context)


def _regressions(
    allocation: CandidateAllocation,
    problem: AllocationProblem,
    context: ObjectiveContext,
) -> int:
    return len(context.baseline_satisfied - allocation.satisfied(problem))


def _progress_fraction(
    allocation: CandidateAllocation, problem: AllocationProblem
) -> Fraction:
    """Sum of per-requirement completion fractions.

    NOT weight-free: a slot in a 2-course requirement contributes 1/2 and one
    in a 5-course requirement 1/5, so this prefers spreading progress across
    small requirements. Phase 4.4 measured that effect. It is provided as an
    explicit, named option - never as a silent default.
    """
    total = Fraction(0)
    for req in problem.requirements:
        if req.needed_count <= 0:
            continue
        held = min(len(allocation.courses_for(req.code)), req.needed_count)
        total += Fraction(held, req.needed_count)
    return total


OBJECTIVE_A = GlobalAllocationObjective(
    name="A_completion_first",
    description="(satisfied, progress, slots) - completion-first, baseline-blind.",
    score=lambda a, p, c: (
        len(a.satisfied(p)),
        _progress_fraction(a, p),
        a.filled_slots,
    ),
)

OBJECTIVE_B = GlobalAllocationObjective(
    name="B_progress_preserving",
    description=(
        "(-regressions, satisfied, progress, slots) - the only objective with a "
        "formal no-regression guarantee; declines completions to keep it."
    ),
    score=lambda a, p, c: (
        -_regressions(a, p, c),
        len(a.satisfied(p)),
        _progress_fraction(a, p),
        a.filled_slots,
    ),
)

OBJECTIVE_C = GlobalAllocationObjective(
    name="C_completion_monotonic",
    description=(
        "(satisfied, -regressions, slots) - completion-first, with regressions "
        "avoided only where that costs no completions. NOT a monotonicity "
        "guarantee. THE ADOPTED OBJECTIVE."
    ),
    score=lambda a, p, c: (
        len(a.satisfied(p)),
        -_regressions(a, p, c),
        a.filled_slots,
    ),
)

#: Today's production behaviour, for measuring against.
OBJECTIVE_SLOTS = GlobalAllocationObjective(
    name="slots_only",
    description="(slots,) - what the current matching maximizes.",
    score=lambda a, p, c: (a.filled_slots,),
)

OBJECTIVES = {
    o.name: o for o in (OBJECTIVE_A, OBJECTIVE_B, OBJECTIVE_C, OBJECTIVE_SLOTS)
}

#: The adopted objective: Policy C with NO partial-progress component.
#:
#: Chosen as a product decision, recorded in DATA_MODEL.md section 21.6.
#: Completions first; among equally-complete allocations, prefer the one
#: that does not undo a requirement the student had already earned; then
#: filled slots as a weight-free tie-break.
#:
#: The progress term was deliberately REMOVED rather than kept: summing
#: per-requirement fractions embeds a weighting nobody stated (it prefers
#: spreading progress across small requirements) and was the measured
#: performance bottleneck.
DEFAULT_OBJECTIVE: GlobalAllocationObjective = OBJECTIVE_C


# --------------------------------------------------------------------------
# decomposition
# --------------------------------------------------------------------------


def decompose(problem: AllocationProblem) -> list[AllocationProblem]:
    """Split into independently solvable sub-problems.

    Two requirements are coupled when a course is eligible for both. Courses
    and requirements in different components share nothing, so an optimum for
    each composes into a global optimum - the objective components are sums
    over requirements and slots, and sums decompose.

    Soundness limit, carried over from Phase 4.3: this holds for LEAF
    satisfaction. An `all_of` group spanning two components would re-couple
    them, because a conjunction is not a sum. Groups consume no slots and do
    not appear in `problem.requirements`, so they cannot leak in here - but
    that also means this function must never be used to optimize a
    group-level objective.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for req in problem.requirements:
        find(f"r:{req.code}")
        for course in req.categories:
            union(f"r:{req.code}", f"c:{course}")

    buckets: dict[str, list[str]] = {}
    for node in parent:
        buckets.setdefault(find(node), []).append(node)

    parts: list[AllocationProblem] = []
    for members in buckets.values():
        codes = {m[2:] for m in members if m.startswith("r:")}
        courses = {m[2:] for m in members if m.startswith("c:")}
        reqs = tuple(r for r in problem.requirements if r.code in codes)
        if not reqs:
            continue
        parts.append(
            AllocationProblem(
                requirements=reqs,
                course_keys=tuple(sorted(courses, key=problem.order_of)),
                share_across_systems=problem.share_across_systems,
                course_order=problem.course_order,
            )
        )
    return sorted(parts, key=lambda p: (p.requirements[0].sort_key, p.requirements[0].code))


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    """An allocation plus an honest account of how it was reached."""

    allocation: CandidateAllocation
    #: True only when EVERY component was searched exhaustively.
    exact: bool
    states_explored: int
    components: int
    #: Components that hit the bound; empty when `exact` is True.
    fallback_components: tuple[str, ...] = ()

    def require_exact(self) -> CandidateAllocation:
        if not self.exact:
            raise OptimizerBoundExceeded(
                f"components {list(self.fallback_components)} exceeded the state "
                "bound; result is not proven optimal"
            )
        return self.allocation


def _choices_for(
    problem: AllocationProblem,
) -> dict[tuple[str, str], list[tuple[str, str] | None]]:
    """Legal (requirement, category) options per course per system, plus None.

    Note what is NOT filtered here: two courses may pick the same category on
    the same requirement. That allocation is legal and merely unsatisfying -
    the Phase 4.2 rule this module must not break.
    """
    systems = (
        tuple(sorted({r.system for r in problem.requirements}))
        if problem.share_across_systems
        else ("*",)
    )
    out: dict[tuple[str, str], list[tuple[str, str] | None]] = {}
    for course in sorted(problem.course_keys, key=problem.order_of):
        for system in systems:
            options: list[tuple[str, str] | None] = [None]
            for req in sorted(problem.requirements, key=lambda r: (r.sort_key, r.code)):
                if req.needed_count <= 0:
                    continue
                if problem.system_of(req.code) != system:
                    continue
                allowed = req.categories.get(course)
                if allowed is None:
                    continue
                if allowed:
                    options.extend((req.code, cat) for cat in sorted(allowed))
                else:
                    options.append((req.code, ""))
            out[(course, system)] = options
    return out


def _solve_component(
    problem: AllocationProblem,
    objective: GlobalAllocationObjective,
    context: ObjectiveContext,
    bound: int,
) -> tuple[CandidateAllocation, bool, int]:
    """Exhaustive search over one component.

    Returns (best, exact, states). `exact` is False only when the bound was
    hit, in which case `best` is the best allocation found so far and the
    caller MUST surface that it is unproven.

    ## Pruning, and why each rule is safe

    1. capacity - a requirement may hold at most `needed_count` courses.
       Extra courses can never raise any objective component: satisfaction is
       a threshold already met, and slots counted beyond capacity are not
       allocations the evaluator would honour.
    2. ordering - courses and options are enumerated in a fixed canonical
       order. This changes only WHICH optimum is found among ties, never the
       optimal score.

    There is deliberately no heuristic pruning. Every rule above is an
    exact-preserving restriction, so a full search still returns the true
    optimum.
    """
    choices = _choices_for(problem)
    # Natural-key order, so both the enumeration and the tie-break below are
    # reproducible across a re-ingest rather than merely deterministic.
    slots = sorted(choices, key=lambda k: (problem.order_of(k[0]), k[1]))
    order = problem.order_of
    capacity = {r.code: r.needed_count for r in problem.requirements}

    best_score: tuple | None = None
    best: CandidateAllocation = CandidateAllocation()
    states = 0
    exact = True
    # Per-requirement occupancy, maintained incrementally. Rescanning the
    # partial assignment for every option made the inner loop quadratic and
    # dominated the runtime; this is a constant-factor change with no effect
    # on which allocations are explored.
    held: dict[str, int] = dict.fromkeys(capacity, 0)

    def recurse(index: int, chosen: list[Assignment]) -> None:
        nonlocal best_score, best, states, exact
        if not exact:
            return
        states += 1
        if states > bound:
            exact = False
            return
        if index == len(slots):
            # `chosen` is already in canonical order: slots are iterated
            # sorted by (course, system) and appended in that order.
            allocation = CandidateAllocation(tuple(chosen))
            score = objective(allocation, problem, context)
            if best_score is None or score > best_score or (
                score == best_score
                and _natural_key(allocation, order) < _natural_key(best, order)
            ):
                best_score = score
                best = allocation
            return

        key = slots[index]
        for option in choices[key]:
            if option is None:
                recurse(index + 1, chosen)
                if not exact:
                    return
                continue
            code, category = option
            if held[code] >= capacity[code]:
                continue
            held[code] += 1
            chosen.append(Assignment(key[0], code, category))
            recurse(index + 1, chosen)
            chosen.pop()
            held[code] -= 1
            if not exact:
                return

    recurse(0, [])
    return best, exact, states


def optimize(
    problem: AllocationProblem,
    objective: GlobalAllocationObjective,
    context: ObjectiveContext | None = None,
    *,
    bound: int = DEFAULT_COMPONENT_BOUND,
) -> OptimizationResult:
    """Optimal allocation under `objective`, by decomposed exhaustive search.

    Decomposition is what makes exhaustive search viable: Phase 4.3 measured a
    realistic 19-course transcript as intractable whole (>5,000,000 states)
    and exact in 17.6 ms once split.

    Per-component results are concatenated. That is sound because components
    share no course and no requirement, so no assignment in one can affect
    the score of another.
    """
    context = context or ObjectiveContext()
    parts = decompose(problem)

    merged = CandidateAllocation()
    total_states = 0
    fallbacks: list[str] = []

    for part in parts:
        allocation, exact, states = _solve_component(part, objective, context, bound)
        total_states += states
        if not exact:
            fallbacks.append(",".join(sorted(r.code for r in part.requirements)))
        merged = merged.merge(allocation)

    merged = CandidateAllocation(
        tuple(
            sorted(
                merged.assignments,
                key=lambda a: (problem.order_of(a.course_key), a.requirement_code),
            )
        )
    )
    return OptimizationResult(
        allocation=merged,
        exact=not fallbacks,
        states_explored=total_states,
        components=len(parts),
        fallback_components=tuple(fallbacks),
    )


__all__ = [
    "DEFAULT_COMPONENT_BOUND",
    "DEFAULT_OBJECTIVE",
    "OBJECTIVES",
    "OBJECTIVE_A",
    "OBJECTIVE_B",
    "OBJECTIVE_C",
    "OBJECTIVE_SLOTS",
    "AllocationProblem",
    "Assignment",
    "CandidateAllocation",
    "GlobalAllocationObjective",
    "ObjectiveContext",
    "OptimizationResult",
    "OptimizerBoundExceeded",
    "RequirementSpec",
    "decompose",
    "optimize",
]
