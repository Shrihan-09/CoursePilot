"""Requirement allocation.

## The problem

A completed course is often ELIGIBLE for several requirements. Suppose a
student completed one course eligible for both "Elective A" and "Elective B",
and a second course eligible only for "Elective A". Allocating greedily in
input order can put the flexible course in A, leaving B unsatisfiable even
though a valid assignment existed.

That is not a hypothetical. It is the normal case in the real Rutgers CS
major, where a single course can appear in the elective list and also count
toward the 300-level constraint.

## Does Rutgers publish allocation rules?

**No.** The catalog prose states the requirements but never says how to
resolve a course that could count in two places. The published text says only
"For details, see a computer science adviser." So CoursePilot must define its
own strategy - and say so, rather than implying Rutgers specified it.

## The strategy: maximum bipartite matching

Modeled explicitly as a constraint problem, per the architecture rule that
search must be visible rather than hidden inside an AI call.

  * Left vertices  - the student's countable courses.
  * Right vertices - requirement SLOTS. A requirement needing N courses
    contributes N slots, so "choose 5 electives" is five interchangeable
    vertices.
  * An edge exists when the course is eligible for that slot's requirement.
  * Maximize the number of filled slots (Kuhn's augmenting-path algorithm).

Maximising filled slots is the objective that most favours the student: it
never reports a requirement unsatisfied when some assignment could have
satisfied it.

## Maximum cardinality alone is NOT enough

Found by running the real CS major against a real record. `01:198:344` is
eligible for both the required `CS_344` node and the `CS_ELECTIVES` pool (it
is a 300-level CS course). Both are one slot, so maximum-cardinality matching
is *indifferent* between them - and it filled an elective slot, reporting a
REQUIRED course as unsatisfied while electives were over-served. Same
cardinality, worse answer.

So slots are prioritised **most-constrained-first**: a slot whose requirement
has few eligible courses is filled before one with many. A `course`
requirement (exactly one course can ever fill it) therefore outranks a
`choose_n` pool of 53 options. This is the classic most-constrained-variable
heuristic, and here it also matches what a student expects: specific
requirements are served before general pools.

It does not reduce the matching size - it only breaks ties among equally large
matchings, choosing the one that serves scarce requirements first.

## Requirement SHARING (Phase 3.75)

The original invariant was **one course fills at most one slot, anywhere**.
That is what a single bipartite matching gives you, and it was correct while
the only modeled system was the major.

It is wrong once Core exists. Rutgers SAS states that "a course used to meet
core goals may also be used to fulfill a major or minor requirement", so a
single course legitimately satisfies one major requirement AND one core
requirement.

The generalisation is deliberately small: every slot carries a
`requirement_system`, and matching runs **once per system**.

  * `EXCLUSIVE` (default) - all slots are treated as one system, so the
    behaviour is byte-for-byte the original single matching.
  * `SHARE_ACROSS_SYSTEMS` - slots are partitioned by system and each
    partition is matched independently. A course can therefore occupy one
    slot in `major` and one in `core`, but never two slots in `major`.

What this deliberately does NOT do:

  * It never shares within a system. "Major A and Major B from one course"
    stays impossible, because each system's matching is still a matching.
  * It never infers permission. A program with no stated policy gets
    EXCLUSIVE, so ambiguity fails safe rather than granting free credit.
  * It does not touch credit accounting. Satisfying two requirements with one
    course does not earn its credits twice - see `engine.py`, where applicable
    credits are summed per student-course, not per allocation.

## Determinism

Maximum matchings are not unique, so the algorithm alone is not enough. Both
sides are sorted before matching:

  * slots   by (eligible-option count, requirement sort_order, code, slot index)
  * courses by (course_string, supplement_code, term_code)

Systems are processed in sorted order too, so a shared allocation is as
reproducible as an exclusive one.

## Credit requirements are NOT matched here (Phase 4.1)

A matching allocates *slots*, and a slot is one course. That is the right unit
for "choose 2 courses" and the wrong one for "6 credits".

Giving a credit requirement one slot per eligible course - the Phase 4
stopgap - let it claim every course it could possibly use rather than the
few it needed. Measured: a 6-credit requirement with five eligible 3-credit
courses claimed all five and starved a competing count requirement of its
only option.

So credit requirements are excluded from the matching and settled afterwards
by `allocate_credits`, which takes courses in descending credit order until
the minimum is met and then stops. Count requirements therefore get first
claim - which is correct, because "exactly one CCD course" is a harder
constraint than "6 credits from any of 59 courses".

## Complexity

O(V * E) per system. With a few hundred courses and a few dozen slots this is
microseconds. If a program ever needs thousands of slots, switch to
Hopcroft-Karp - the interface here does not change.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

# Used when a program is EXCLUSIVE: every slot is placed in one pseudo-system
# so a single matching covers the whole program.
_ALL_SYSTEMS = "*"


@dataclass(frozen=True, slots=True)
class Slot:
    """One unit of demand from a requirement.

    `option_count` is how many courses are eligible for this slot's
    requirement. Slots with fewer options are filled first - see the
    most-constrained-first note above.

    `system` is which body of requirements the slot belongs to. It is what
    makes sharing expressible without special-casing any particular system by
    name in the allocator.
    """

    requirement_code: str
    slot_index: int
    sort_key: tuple
    option_count: int = 0
    system: str = "major"

    @property
    def key(self) -> tuple[str, int]:
        return (self.requirement_code, self.slot_index)

    @property
    def priority(self) -> tuple:
        """Scarcest requirements first, then the declared order."""
        return (self.option_count, self.sort_key, self.slot_index)


@dataclass(frozen=True, slots=True)
class Candidate:
    """One countable course on the student's record."""

    course_key: str          # stable identity: course_id + term
    sort_key: tuple
    eligible_requirements: frozenset[str]


@dataclass(slots=True)
class AllocationPlan:
    """Result of matching.

    A course may now appear in more than one slot - at most one per system -
    so `slots_for_course` returns a LIST. `by_slot` remains one course per
    slot, which is still true regardless of sharing.
    """

    by_slot: dict[tuple[str, int], str] = field(default_factory=dict)
    _by_course: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    # Which system each slot was matched in. Kept so the audit can explain
    # *why* a course counted twice.
    slot_system: dict[tuple[str, int], str] = field(default_factory=dict)

    def courses_for(self, requirement_code: str) -> list[str]:
        return [
            course
            for (req, _), course in sorted(self.by_slot.items())
            if req == requirement_code
        ]

    def slots_for_course(self, course_key: str) -> list[tuple[str, int]]:
        return sorted(self._by_course.get(course_key, []))

    def systems_for_course(self, course_key: str) -> list[str]:
        return sorted(
            {self.slot_system.get(slot, "") for slot in self._by_course.get(course_key, [])}
        )

    @property
    def allocated_course_keys(self) -> set[str]:
        return set(self._by_course)

    # Which category each slot was filled under, for requirements whose
    # source certifies sub-categories. Empty for every other requirement,
    # which is most of them.
    slot_category: dict[tuple[str, int], str] = field(default_factory=dict)

    def categories_for(self, requirement_code: str) -> list[str]:
        """The categories actually SELECTED for this requirement, in slot order.

        Not the categories the allocated courses could have counted as - the
        edges the allocation chose. A course certified Xp and Xq appears here
        exactly once, under one of them.
        """
        return [
            self.slot_category[key]
            for key in sorted(self.slot_category)
            if key[0] == requirement_code and self.slot_category[key]
        ]

    def release(self, requirement_code: str) -> None:
        """Drop every allocation belonging to one requirement.

        Used by the category-aware pass, which re-chooses a requirement's
        courses locally. Only that requirement's slots are touched; every
        other requirement keeps the courses the matching gave it.
        """
        for slot_key in [k for k in self.by_slot if k[0] == requirement_code]:
            course_key = self.by_slot.pop(slot_key)
            self.slot_system.pop(slot_key, None)
            self.slot_category.pop(slot_key, None)
            slots = self._by_course.get(course_key, [])
            if slot_key in slots:
                slots.remove(slot_key)
            if not slots:
                self._by_course.pop(course_key, None)

    def assign(
        self,
        requirement_code: str,
        slot_index: int,
        course_key: str,
        system: str,
        category: str = "",
    ) -> None:
        """Record an allocation made outside the matching.

        Used by the post-matching credit pass and by the category-aware pass.
        Kept on the plan so every allocation, however it was decided, is
        visible in one place.
        """
        slot_key = (requirement_code, slot_index)
        self.by_slot[slot_key] = course_key
        self._by_course.setdefault(course_key, []).append(slot_key)
        self.slot_system[slot_key] = system
        if category:
            self.slot_category[slot_key] = category

    @property
    def shared_course_keys(self) -> set[str]:
        """Courses that filled a slot in more than one system."""
        return {c for c, slots in self._by_course.items() if len(slots) > 1}


def _match_one_system(
    slots: list[Slot], candidates: list[Candidate]
) -> dict[tuple[str, int], str]:
    """Maximum bipartite matching within a single system.

    Unchanged from the original algorithm - the sharing generalisation happens
    by calling this once per system, not by altering the matching itself.
    """
    ordered_slots = sorted(slots, key=lambda s: s.priority)
    ordered_candidates = sorted(candidates, key=lambda c: c.sort_key)
    by_key = {c.course_key: c for c in ordered_candidates}

    slot_to_course: dict[tuple[str, int], str] = {}

    def try_assign(candidate: Candidate, visited: set[tuple[str, int]]) -> bool:
        """Kuhn's algorithm: find an augmenting path for this course."""
        for slot in ordered_slots:
            if slot.requirement_code not in candidate.eligible_requirements:
                continue
            if slot.key in visited:
                continue
            visited.add(slot.key)

            holder = slot_to_course.get(slot.key)
            if holder is None:
                slot_to_course[slot.key] = candidate.course_key
                return True

            # Slot taken - can its current occupant move elsewhere?
            if try_assign(by_key[holder], visited):
                slot_to_course[slot.key] = candidate.course_key
                return True
        return False

    for candidate in ordered_candidates:
        try_assign(candidate, set())

    return slot_to_course


def allocate(
    slots: list[Slot],
    candidates: list[Candidate],
    *,
    share_across_systems: bool = False,
) -> AllocationPlan:
    """Allocate courses to requirement slots.

    With `share_across_systems=False` (the default) this is exactly the
    original single matching: one course fills at most one slot in the whole
    program.

    With `share_across_systems=True` the slots are partitioned by system and
    matched independently, so one course may fill one slot per system - and
    still never two slots within a system.
    """
    if share_across_systems:
        groups: dict[str, list[Slot]] = defaultdict(list)
        for slot in slots:
            groups[slot.system].append(slot)
    else:
        groups = {_ALL_SYSTEMS: list(slots)}

    # The partition key is an internal detail of the matching. What gets
    # RECORDED is always the slot's own system, so an EXCLUSIVE program (whose
    # slots are all matched in one pseudo-partition) still reports real system
    # names rather than the placeholder.
    system_of_slot = {slot.key: slot.system for slot in slots}

    plan = AllocationPlan()
    # Sorted so the outcome does not depend on dict insertion order.
    for partition in sorted(groups):
        matched = _match_one_system(groups[partition], candidates)
        for slot_key, course_key in matched.items():
            plan.by_slot[slot_key] = course_key
            plan._by_course.setdefault(course_key, []).append(slot_key)
            plan.slot_system[slot_key] = system_of_slot[slot_key]

    return plan


def max_distinct_categories(
    course_categories: dict[str, frozenset[str]],
) -> tuple[int, dict[str, str]]:
    """How many DISTINCT categories a set of courses can cover at once.

    Rutgers SAS: "Students must take two degree credit-bearing courses and
    meet at least two of these goals." A course certified for both AHp and
    AHq can only be counted under ONE of them, so the question is a matching,
    not a set union:

        two courses, both AHp        -> 1 distinct goal
        two courses, AHp and AHq     -> 2
        one course, AHp AND AHq      -> 1 (one course, one goal slot)

    Taking the union of categories would answer 2 for the last case and
    wrongly satisfy the requirement with a single course.

    Reuses the same augmenting-path search as slot allocation, with
    categories playing the part of slots. Deterministic: both sides are
    sorted before matching.

    Returns (count, {course_key: category}).
    """
    courses = sorted(course_categories)
    categories = sorted({c for cats in course_categories.values() for c in cats})

    category_to_course: dict[str, str] = {}

    def try_assign(course: str, visited: set[str]) -> bool:
        for category in categories:
            if category not in course_categories[course] or category in visited:
                continue
            visited.add(category)
            holder = category_to_course.get(category)
            if holder is None or try_assign(holder, visited):
                category_to_course[category] = course
                return True
        return False

    for course in courses:
        try_assign(course, set())

    return len(category_to_course), {c: cat for cat, c in category_to_course.items()}


def allocate_credits(
    requirement_code: str,
    needed: "Decimal",
    candidates: list[tuple[str, "Decimal | None", str]],
) -> list[str]:
    """Claim just enough courses to meet a credit minimum.

    `candidates` is (course_key, credits, tie_break) for courses eligible for
    this requirement and not already claimed in its system.

    Highest credits first, stopping as soon as the minimum is reached, so a
    credit requirement never holds courses it does not need.

    `tie_break` exists because `course_key` is built from a surrogate id.
    Ordering equal-credit courses by it would be deterministic within one
    database and arbitrary across two: re-ingesting the catalog would silently
    change WHICH course the audit says filled the requirement. The caller
    passes a stable natural identity (the course string) instead, so the same
    student record always produces the same explanation.

    A course whose credits are unknown contributes zero and therefore sorts
    last. It is never assumed to be worth 3.

    Rutgers states a MINIMUM ("6 credits"), so overshooting is fine - 4 + 3 = 7
    satisfies a 6-credit requirement. No rounding or truncation is invented.
    """
    chosen: list[str] = []
    total = Decimal(0)
    for course_key, credits, tie_break in sorted(
        candidates, key=lambda x: (-(x[1] or Decimal(0)), x[2], x[0])
    ):
        if total >= needed:
            break
        chosen.append(course_key)
        total += credits or Decimal(0)
    return chosen
