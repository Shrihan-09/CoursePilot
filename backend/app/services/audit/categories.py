"""Category-aware allocation for distinct-category requirements (Phase 4.2).

## The defect this fixes

Phase 4.1 gave a requirement two independent conditions - a course count and
a distinct-category count - and taught the EVALUATOR to check both. It did
not teach the ALLOCATOR that categories exist.

So for a requirement needing 2 courses in 2 distinct categories, with

    01:013:120 -> Xp      01:070:102 -> Xp      01:070:201 -> Xo

the ordinary matching fills its two slots with the first two courses in sort
order - both Xp - and the evaluator then correctly reports the requirement
unsatisfied. The audit explains a failure it caused itself, while the student
holds a satisfying pair.

## The invariant that must survive

    one StudentCourse -> at most ONE allocation within a requirement

A course certified for Xp AND Xq is eligible twice and satisfies once. It may
not occupy two category positions in the same requirement. That is not an
implementation convenience: SAS states for Arts and Humanities that students
must take "two degree credit-bearing courses and meet at least two of these
goals", and the Core FAQ says a course on both the HST and SCL lists still
leaves the student needing "TWO courses". One course, one slot.

Cross-system sharing is untouched. A course may still fill one slot in
`major` and one in `core`; this module only ever looks inside a single
requirement.

## Two strategies, one interface

Both implement `CategoryAllocationStrategy.select`.

`CategorySlotStrategy` - builds one vertex per AVAILABLE CATEGORY and runs a
matching of courses against categories. Polynomial, O(V*E).

`CategoryCoverageStrategy` - enumerates course subsets and scores each by
category coverage, then by filled slots. Exponential in the number of
candidates, and used as an independent oracle rather than a production path.

They are provably equivalent in the value they achieve - see
`docs/DATA_MODEL.md` section 18 - and `test_category_allocation.py` checks
that empirically on every case.

## Why one vertex per category, and not per required category

"min_distinct_categories = 2" suggests two generic category slots. That
representation is WRONG: two courses both certified Xp would fill both
generic slots and report two categories covered. The slots have to BE the
categories, so that a category can be occupied only once.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Protocol

# Enumeration guard for the coverage strategy. Real requirements are far
# below this (SAS Core's largest is 2 courses from a handful of held
# candidates), but an unbounded C(n, k) is not something to leave in a code
# path that any curriculum can reach.
MAX_COVERAGE_COMBINATIONS = 20_000


@dataclass(frozen=True, slots=True)
class CategoryCandidate:
    """One of the student's courses, with the categories that certify it.

    `categories` may be empty: a course can be eligible for a requirement
    without the source certifying it under any sub-category.
    """

    course_key: str
    sort_key: tuple
    categories: frozenset[str]


@dataclass(frozen=True, slots=True)
class CategoryRequest:
    """Everything one requirement needs to make a category-aware choice.

    Deliberately carries no Requirement, no Session and no course objects:
    the strategies are pure functions of this, which is what makes comparing
    two of them on identical input meaningful.
    """

    requirement_code: str
    needed_count: int
    needed_categories: int
    candidates: tuple[CategoryCandidate, ...]


@dataclass(frozen=True, slots=True)
class CategoryAllocation:
    """The chosen courses, each paired with the category it was counted under.

    The pairing matters. A course certified Xp and Xq contributes exactly one
    of them, and the audit must be able to say which, so the reported
    category is the EDGE the allocation selected - never the union of what
    the course could have counted as.
    """

    assignments: tuple[tuple[str, str], ...]

    @property
    def course_keys(self) -> tuple[str, ...]:
        return tuple(course for course, _ in self.assignments)

    @property
    def distinct_categories(self) -> int:
        return len({category for _, category in self.assignments if category})

    @property
    def filled_slots(self) -> int:
        return len(self.assignments)

    def signature(self) -> tuple:
        """Canonical form for comparing two strategies' results.

        Two allocations are semantically equivalent when they fill the same
        number of slots and cover the same number of distinct categories
        under the same single-use rule. WHICH courses were chosen may
        legitimately differ when several optima exist.
        """
        return (self.filled_slots, self.distinct_categories)


class CategoryAllocationStrategy(Protocol):
    """Chooses which of a requirement's candidate courses to allocate."""

    name: str

    def select(self, request: CategoryRequest) -> CategoryAllocation: ...


def _match_courses_to_categories(
    candidates: tuple[CategoryCandidate, ...],
) -> dict[str, str]:
    """Maximum matching of courses against CATEGORIES.

    Kuhn's augmenting-path search, the same one used for requirement slots,
    with categories as the right-hand vertices. A matching is exactly the
    single-use rule: each course takes at most one category and each category
    is taken at most once.

    Deterministic: both sides are sorted before the search, and the sort key
    is the course's natural identity, so re-ingesting the catalog cannot
    change the answer.

    Returns {course_key: category}.
    """
    ordered = sorted(candidates, key=lambda c: (c.sort_key, c.course_key))
    categories = sorted({c for cand in ordered for c in cand.categories})
    by_key = {c.course_key: c for c in ordered}

    category_to_course: dict[str, str] = {}

    def try_assign(course_key: str, visited: set[str]) -> bool:
        candidate = by_key[course_key]
        for category in categories:
            if category not in candidate.categories or category in visited:
                continue
            visited.add(category)
            holder = category_to_course.get(category)
            if holder is None or try_assign(holder, visited):
                category_to_course[category] = course_key
                return True
        return False

    for candidate in ordered:
        try_assign(candidate.course_key, set())

    return {course: category for category, course in category_to_course.items()}


def _fill_remaining(
    chosen: list[tuple[str, str]],
    request: CategoryRequest,
) -> list[tuple[str, str]]:
    """Top the selection up to the requirement's course count.

    Once the distinct categories are covered, the requirement may still need
    more COURSES - "3 courses meeting at least 2 goals" is a legitimate
    shape. The extra courses are taken in deterministic order and reported
    under a category they actually hold, which by construction is one already
    covered, so the distinct count does not move.
    """
    taken = {course for course, _ in chosen}
    remaining = sorted(
        (c for c in request.candidates if c.course_key not in taken),
        key=lambda c: (c.sort_key, c.course_key),
    )
    for candidate in remaining:
        if len(chosen) >= request.needed_count:
            break
        category = min(candidate.categories) if candidate.categories else ""
        chosen.append((candidate.course_key, category))
    return chosen


class CategorySlotStrategy:
    """STRATEGY A - one vertex per available category, solved by matching.

    The requirement's categories become the right-hand side of a bipartite
    graph and the student's eligible courses the left. Maximum matching gives
    the largest set of (course, category) pairs with no course and no
    category reused - which is precisely "how many distinct categories can
    these courses cover at once".

    A multi-category course therefore competes for one category and can be
    displaced to another by an augmenting path, which is how
    `A -> {Xp, Xq}` plus `B -> {Xp}` reaches two categories instead of one.
    """

    name = "category_slot"

    def select(self, request: CategoryRequest) -> CategoryAllocation:
        matched = _match_courses_to_categories(request.candidates)

        # Covered pairs first, in deterministic order, capped at the course
        # count. Every matched pair carries a distinct category, so any
        # needed_count of them cover needed_count categories.
        sort_key = {c.course_key: c.sort_key for c in request.candidates}
        pairs = sorted(matched.items(), key=lambda kv: (sort_key[kv[0]], kv[0]))
        chosen = [pair for pair in pairs[: request.needed_count]]

        return CategoryAllocation(tuple(_fill_remaining(chosen, request)))


class CategoryCoverageStrategy:
    """STRATEGY B - enumerate course subsets, score by category coverage.

    Treats categories as an OBJECTIVE rather than as physical positions:
    every subset of the right size is scored by how many distinct categories
    it can cover, then by how many slots it fills, then by a deterministic
    canonical ordering.

    Coverage of a subset is itself a matching, because a course in the subset
    still cannot occupy two categories. So this strategy does not avoid the
    matching - it wraps a search around it.

    COMPLEXITY: C(n, k) subsets. That is exponential in general, which is why
    this is an oracle for testing rather than the production path, and why
    `MAX_COVERAGE_COMBINATIONS` bounds it. It is kept because an independent
    implementation that agrees is real evidence; a second copy of the same
    algorithm would be none.
    """

    name = "category_coverage"

    def select(self, request: CategoryRequest) -> CategoryAllocation:
        candidates = sorted(
            request.candidates, key=lambda c: (c.sort_key, c.course_key)
        )
        take = min(request.needed_count, len(candidates))
        if take <= 0:
            return CategoryAllocation(())

        best: tuple | None = None
        best_canonical: tuple = ()
        best_assignments: tuple[tuple[str, str], ...] = ()
        examined = 0

        for subset in combinations(candidates, take):
            examined += 1
            if examined > MAX_COVERAGE_COMBINATIONS:
                # Refuse rather than silently return a worse answer.
                raise ValueError(
                    f"{request.requirement_code}: coverage search exceeded "
                    f"{MAX_COVERAGE_COMBINATIONS} combinations "
                    f"({len(candidates)} candidates, choose {take}). "
                    "Use CategorySlotStrategy."
                )
            matched = _match_courses_to_categories(tuple(subset))
            assignments = tuple(
                (c.course_key, matched.get(c.course_key, ""))
                for c in subset
            )
            covered = len({cat for _, cat in assignments if cat})
            # Lexicographic: coverage first, then slots filled, then the
            # canonical course ordering as the deterministic tie-break.
            key = (covered, len(assignments))
            canonical = tuple(c.sort_key for c in subset)
            if best is None or key > best or (key == best and canonical < best_canonical):
                best = key
                best_canonical = canonical
                best_assignments = assignments

        return CategoryAllocation(best_assignments)


#: Production strategy. See DATA_MODEL.md section 18 for why.
DEFAULT_STRATEGY: CategoryAllocationStrategy = CategorySlotStrategy()

ALL_STRATEGIES: tuple[CategoryAllocationStrategy, ...] = (
    CategorySlotStrategy(),
    CategoryCoverageStrategy(),
)
