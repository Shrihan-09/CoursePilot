"""Brute-force allocation oracle (Phase 4.3 investigation).

NOT A PRODUCTION PATH. This enumerates every valid allocation and returns the
mathematically optimal one under a stated objective. It exists so that
faster, cleverer algorithms have something to be wrong against.

It is deliberately slow, deliberately simple, and deliberately independent of
`allocation.py` and `categories.py`: an oracle that shares code with the thing
it checks proves nothing. Nothing here is imported by the audit engine.

## The formal model

Given

    C    the student's countable courses
    R    requirements that consume course slots
    n(r) how many courses requirement r demands      (its THRESHOLD)
    d(r) how many DISTINCT categories r demands      (0 if none)
    E    eligibility, E(c, r) true iff c may count toward r
    K    categories, K(c, r) the categories certifying c for r
    sys  the requirement system of r
    S    whether the program shares across systems

an ALLOCATION is a set of triples (course, requirement, category) with

    (1) capacity     at most n(r) courses allocated to r
    (2) single use   a course appears at most once PER SYSTEM
                     (and at most once overall when S is false)
    (3) eligibility  only where E(c, r)
    (4) category     each chosen category is one the course actually holds

Note what (4) does NOT say. Two courses MAY be allocated to the same
requirement under the same category: that is a legal allocation which simply
does not satisfy a distinctness minimum. Distinctness is a property of
SATISFACTION, not of validity - treating it as a validity constraint would
make the two-same-category case unrepresentable instead of unsatisfying.

and requirement r is SATISFIED iff it holds n(r) courses AND covers d(r)
distinct categories.

Constraint (4) is what makes this richer than a plain course -> slot
assignment: the same pair (c, r) can be legal under one category and useless
under another.

## The objective

Lexicographic, highest priority first:

    1. number of SATISFIED requirements
    2. number of filled slots
    3. deterministic canonical ordering

Priority 1 is what Rutgers describes ("DN will always adjust the audit so
that the maximum number of requirements are complete" - SAS Academic
Advising; see DATA_MODEL.md 17.7). Priority 2 is the current production
objective, demoted. Priority 3 exists so the answer is reproducible.

`objective` is a parameter, not a constant, because comparing objectives is
the point of the phase.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

# Hard stop on enumeration. An oracle that silently gives up would be worse
# than no oracle: it would agree with whatever it was checking.
MAX_ORACLE_STATES = 2_000_000


class OracleTooLarge(RuntimeError):
    """The instance exceeded the enumeration bound. Never a wrong answer."""


@dataclass(frozen=True, slots=True)
class OracleRequirement:
    code: str
    system: str
    needed_count: int
    needed_categories: int = 0
    # course_key -> the categories certifying that course for THIS requirement
    categories: Mapping[str, frozenset[str]] = field(default_factory=dict)
    sort_key: tuple = ()

    def eligible_courses(self) -> frozenset[str]:
        return frozenset(self.categories)


@dataclass(frozen=True, slots=True)
class OracleProblem:
    """One student's allocation instance, as pure data."""

    requirements: tuple[OracleRequirement, ...]
    course_keys: tuple[str, ...]
    share_across_systems: bool = False

    def systems(self) -> tuple[str, ...]:
        if not self.share_across_systems:
            # EXCLUSIVE: every requirement competes in one pool.
            return ("*",)
        return tuple(sorted({r.system for r in self.requirements}))

    def system_of(self, code: str) -> str:
        if not self.share_across_systems:
            return "*"
        return next(r.system for r in self.requirements if r.code == code)


@dataclass(frozen=True, slots=True)
class OracleAllocation:
    """(course_key, requirement_code, category) triples, canonically sorted."""

    triples: tuple[tuple[str, str, str], ...] = ()

    @property
    def filled_slots(self) -> int:
        return len(self.triples)

    def courses_for(self, code: str) -> tuple[str, ...]:
        return tuple(c for c, r, _ in self.triples if r == code)

    def categories_for(self, code: str) -> tuple[str, ...]:
        return tuple(cat for _, r, cat in self.triples if r == code and cat)

    def satisfied(self, problem: OracleProblem) -> frozenset[str]:
        out = set()
        for req in problem.requirements:
            if req.needed_count <= 0:
                continue
            held = self.courses_for(req.code)
            if len(held) < req.needed_count:
                continue
            if req.needed_categories:
                if len(set(self.categories_for(req.code))) < req.needed_categories:
                    continue
            out.add(req.code)
        return frozenset(out)

    def is_valid(self, problem: OracleProblem) -> bool:
        """Every constraint re-checked from scratch.

        A separate check rather than a guarantee of construction, so a bug in
        the enumerator surfaces as an invalid allocation rather than as a
        confidently wrong optimum.
        """
        by_req: dict[str, list[tuple[str, str]]] = {}
        for course, code, category in self.triples:
            by_req.setdefault(code, []).append((course, category))

        req_by_code = {r.code: r for r in problem.requirements}
        for code, entries in by_req.items():
            req = req_by_code.get(code)
            if req is None:
                return False
            if len(entries) > req.needed_count:      # (1) capacity
                return False
            courses = [c for c, _ in entries]
            if len(courses) != len(set(courses)):    # one course once per req
                return False
            for course, category in entries:
                allowed = req.categories.get(course)
                if allowed is None:                  # (3) eligibility
                    return False
                if category and category not in allowed:
                    return False
                if not category and allowed:
                    # A course with categories must be counted under one.
                    return False

        # (2) single use per system
        per_system: dict[tuple[str, str], int] = {}
        for course, code, _ in self.triples:
            key = (course, problem.system_of(code))
            per_system[key] = per_system.get(key, 0) + 1
        return all(count <= 1 for count in per_system.values())


def lexicographic_objective(
    allocation: OracleAllocation, problem: OracleProblem
) -> tuple:
    """Objective C: completions first, then slots. See the module docstring."""
    return (len(allocation.satisfied(problem)), allocation.filled_slots)


def max_slots_objective(allocation: OracleAllocation, problem: OracleProblem) -> tuple:
    """Objective B: today's production objective, for comparison."""
    return (allocation.filled_slots,)


def max_satisfied_objective(
    allocation: OracleAllocation, problem: OracleProblem
) -> tuple:
    """Objective A: completions only, indifferent to everything else."""
    return (len(allocation.satisfied(problem)),)


def solve(
    problem: OracleProblem,
    objective: Callable[[OracleAllocation, OracleProblem], tuple] = lexicographic_objective,
    *,
    max_states: int = MAX_ORACLE_STATES,
) -> OracleAllocation:
    """Exhaustive search for the optimal allocation.

    Enumerates, for every course and every system, which requirement (if any)
    that course serves there - then every legal category for that pairing.
    No pruning beyond legality, because pruning is where an oracle acquires
    the same blind spots as the algorithm it is meant to check.
    """
    requirements = sorted(problem.requirements, key=lambda r: (r.sort_key, r.code))
    courses = sorted(problem.course_keys)
    systems = problem.systems()

    # For each (course, system): the legal (requirement, category) choices,
    # plus None for "not used here".
    choices: dict[tuple[str, str], list[tuple[str, str] | None]] = {}
    for course in courses:
        for system in systems:
            options: list[tuple[str, str] | None] = [None]
            for req in requirements:
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
            choices[(course, system)] = options

    slots: list[tuple[str, str]] = [(c, s) for c in courses for s in systems]

    budget = 1
    for key in slots:
        budget *= len(choices[key])
        if budget > max_states:
            raise OracleTooLarge(
                f"{budget if budget <= max_states else '>'}{max_states} states for "
                f"{len(courses)} courses x {len(systems)} system(s). "
                "Shrink the case or raise max_states deliberately."
            )

    best: tuple | None = None
    best_allocation = OracleAllocation()
    capacity = {r.code: r.needed_count for r in requirements}

    def recurse(index: int, chosen: list[tuple[str, str, str]]) -> None:
        nonlocal best, best_allocation
        if index == len(slots):
            allocation = OracleAllocation(tuple(sorted(chosen)))
            if not allocation.is_valid(problem):
                return
            score = objective(allocation, problem)
            if best is None or score > best or (
                score == best and allocation.triples < best_allocation.triples
            ):
                best = score
                best_allocation = allocation
            return

        course, system = slots[index]
        for option in choices[(course, system)]:
            if option is None:
                recurse(index + 1, chosen)
                continue
            code, category = option
            used = [t for t in chosen if t[1] == code]
            if len(used) >= capacity[code]:
                continue
            chosen.append((course, code, category))
            recurse(index + 1, chosen)
            chosen.pop()

    recurse(0, [])
    return best_allocation


def enumerate_optima(
    problem: OracleProblem,
    objective: Callable[[OracleAllocation, OracleProblem], tuple] = lexicographic_objective,
) -> tuple[tuple, list[OracleAllocation]]:
    """Every allocation attaining the optimal score.

    Used to show when an objective is INDIFFERENT between outcomes a student
    would not consider equivalent - which is how a product decision gets
    identified rather than assumed.
    """
    best = solve(problem, objective)
    best_score = objective(best, problem)

    found: list[OracleAllocation] = []
    requirements = sorted(problem.requirements, key=lambda r: (r.sort_key, r.code))
    courses = sorted(problem.course_keys)
    systems = problem.systems()
    capacity = {r.code: r.needed_count for r in requirements}

    choices: dict[tuple[str, str], list[tuple[str, str] | None]] = {}
    for course in courses:
        for system in systems:
            options: list[tuple[str, str] | None] = [None]
            for req in requirements:
                if req.needed_count <= 0 or problem.system_of(req.code) != system:
                    continue
                allowed = req.categories.get(course)
                if allowed is None:
                    continue
                if allowed:
                    options.extend((req.code, cat) for cat in sorted(allowed))
                else:
                    options.append((req.code, ""))
            choices[(course, system)] = options

    slots = [(c, s) for c in courses for s in systems]
    seen: set[tuple] = set()

    def recurse(index: int, chosen: list[tuple[str, str, str]]) -> None:
        if index == len(slots):
            allocation = OracleAllocation(tuple(sorted(chosen)))
            if not allocation.is_valid(problem):
                return
            if objective(allocation, problem) == best_score:
                if allocation.triples not in seen:
                    seen.add(allocation.triples)
                    found.append(allocation)
            return
        course, system = slots[index]
        for option in choices[(course, system)]:
            if option is None:
                recurse(index + 1, chosen)
                continue
            code, category = option
            used = [t for t in chosen if t[1] == code]
            if len(used) >= capacity[code]:
                continue
            chosen.append((course, code, category))
            recurse(index + 1, chosen)
            chosen.pop()

    recurse(0, [])
    return best_score, found


def connected_components(problem: OracleProblem) -> list[OracleProblem]:
    """Split the instance into independently solvable sub-instances.

    Two requirements are coupled when some course is eligible for both; a
    course is coupled to every requirement it is eligible for. Components of
    that graph share no course and no constraint, so an optimum for each is
    an optimum overall - the objective is a SUM over requirements and slots,
    and sums decompose.

    The caveat that matters: this holds for LEAF satisfaction. An `all_of`
    group spanning two components couples them at the group level, because
    the group's own satisfaction is not the sum of its parts. Groups consume
    no slots, so they do not appear here - which is exactly why this function
    cannot be used to decide group-level objectives.
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

    groups: dict[str, list[str]] = {}
    for node in parent:
        groups.setdefault(find(node), []).append(node)

    out: list[OracleProblem] = []
    for members in groups.values():
        codes = {m[2:] for m in members if m.startswith("r:")}
        course_keys = {m[2:] for m in members if m.startswith("c:")}
        reqs = tuple(r for r in problem.requirements if r.code in codes)
        if not reqs:
            continue
        out.append(
            OracleProblem(
                requirements=reqs,
                course_keys=tuple(sorted(course_keys)),
                share_across_systems=problem.share_across_systems,
            )
        )
    return sorted(out, key=lambda p: (p.requirements[0].sort_key, p.requirements[0].code))


__all__ = [
    "MAX_ORACLE_STATES",
    "OracleAllocation",
    "OracleProblem",
    "OracleRequirement",
    "OracleTooLarge",
    "connected_components",
    "enumerate_optima",
    "lexicographic_objective",
    "max_satisfied_objective",
    "max_slots_objective",
    "solve",
]
