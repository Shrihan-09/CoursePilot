"""Prerequisite dependency paths for planning (Phase 6.5).

Question answered: "which courses must the plan ADD, in earlier terms, so that
course X's published prerequisite can be met?" - not "is X's prerequisite
met?", which stays with app.services.prerequisites / course_eligibility.

The prerequisite expression (Phase 6.2 IR) is walked WITHOUT flattening:

| node | path |
|---|---|
| course K, already available | nothing |
| course K, not available | K's own path, then K |
| AND (all of) | the union of every child's path |
| OR (any of) | ONE child - the cheapest by the key below |
| at least N of | the N cheapest children |
| UNSUPPORTED (Phase 6.2/6.4 could not interpret it) | infeasible - never planned around |
| course the caller cannot plan (`plannable` names why: no offering evidence...) | infeasible |

`(A and B) or C` therefore yields either {A, B} or {C}, never {A, B, C}.

## Choosing among alternatives (deterministic)

    key = (number of courses the alternative adds,
           - number of those courses that themselves count toward a remaining requirement,
           canonical text of the alternative)

Fewest added courses first; among equals, the one whose courses also make
degree progress; then the canonical text (Phase 6.2's canonical form), so the
choice never depends on dictionary or database order.

## Cycles and depth

A course that (transitively) requires itself is reported with the cycle
path; recursion is capped at `max_depth` levels. Neither loops.

"Available" means already passed (or in progress) per the Degree/eligibility
layer, or already planned. This module never decides grades: a course whose
recorded grade is too low for the target's minimum-grade condition is passed
in as NOT available by the caller (which reads Phase 6.4's grade evidence),
and so is planned as a retake.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.domain.prerequisites import (
    AllOf,
    AnyOf,
    AtLeast,
    ConcurrentReq,
    CourseReq,
    Expr,
    Unsupported,
    to_text,
)


@dataclass(frozen=True, slots=True)
class PathResult:
    feasible: bool
    #: Courses to add, prerequisites before the courses that need them.
    courses: tuple[str, ...] = ()
    reason: str | None = None
    cycle: tuple[str, ...] = ()


@dataclass(slots=True)
class DependencyPlanner:
    #: course -> its published prerequisite IR; None when none is published.
    #: Returns the sentinel `UNREADABLE` for a published but uninterpreted one.
    expression_of: Callable[[str], Expr | None]
    available: set[str]
    contributes: Callable[[str], bool] = lambda _course: False
    #: course -> None if it may be added to a plan, else the reason it may
    #: not. A course CoursePilot has no data for is NOT a free course with no
    #: prerequisite (Phase 6.5 finding: Newark's 21:640:114 looked "free").
    plannable: Callable[[str], str | None] = lambda _course: None
    max_depth: int = 6
    _memo: dict[str, PathResult] = field(default_factory=dict)

    def path_for(self, course: str, unavailable: frozenset[str] = frozenset()) -> PathResult:
        """Courses to add so `course` becomes takeable (`course` itself excluded).

        `unavailable` - courses the caller knows do NOT count as met for this
        target (e.g. a recorded D where the target needs C): they must be
        planned again (a retake)."""
        if unavailable:
            saved = (set(self.available), dict(self._memo))
            self.available -= set(unavailable)
            self._memo.clear()
            try:
                return self._need(course, ())
            finally:
                self.available, self._memo = saved[0], saved[1]
        return self._need(course, ())

    # ------------------------------------------------------------------ #

    def _need(self, course: str, stack: tuple[str, ...]) -> PathResult:
        if course in stack:
            cycle = stack[stack.index(course):] + (course,)
            return PathResult(False, reason="dependency_cycle", cycle=cycle)
        if len(stack) > self.max_depth:
            return PathResult(False, reason="depth_limit")
        if not stack and course in self._memo:
            return self._memo[course]
        expr = self.expression_of(course)
        if expr is None:
            result = PathResult(True)
        elif expr is UNREADABLE:
            result = PathResult(False, reason=f"unreadable_prerequisite:{course}")
        else:
            result = self._solve(expr, stack + (course,))
        if result.feasible and not stack:
            self._memo[course] = result
        return result

    def _solve(self, e: Expr, stack: tuple[str, ...]) -> PathResult:
        if isinstance(e, CourseReq | ConcurrentReq):
            key = e.course_key
            if key in self.available:
                return PathResult(True)
            blocked = self.plannable(key)
            if blocked:
                return PathResult(False, reason=f"{blocked}:{key}")
            sub = self._need(key, stack)
            if not sub.feasible:
                return sub
            return PathResult(True, _union(sub.courses, (key,)))
        if isinstance(e, Unsupported):
            return PathResult(False, reason=f"unsupported:{e.reason}")
        children = [(c, self._solve(c, stack)) for c in e.children]
        if isinstance(e, AllOf):
            courses: tuple[str, ...] = ()
            for _, r in children:
                if not r.feasible:
                    return r
                courses = _union(courses, r.courses)
            return PathResult(True, courses)
        feasible = sorted(((self._key(c, r), r) for c, r in children if r.feasible),
                          key=lambda kr: kr[0])
        need = 1 if isinstance(e, AnyOf) else e.n if isinstance(e, AtLeast) else 1
        if len(feasible) < need:
            failed = next((r for _, r in children if not r.feasible), None)
            return PathResult(False, reason=failed.reason if failed else "no_alternative",
                              cycle=failed.cycle if failed else ())
        courses = ()
        for _, r in feasible[:need]:
            courses = _union(courses, r.courses)
        return PathResult(True, courses)

    def _key(self, child: Expr, r: PathResult) -> tuple:
        return (len(r.courses), -sum(1 for c in r.courses if self.contributes(c)), to_text(child))


class _Unreadable:
    def __repr__(self) -> str:
        return "UNREADABLE"


#: A published prerequisite CoursePilot could not interpret.
UNREADABLE = _Unreadable()


def _union(a: tuple[str, ...], b: tuple[str, ...]) -> tuple[str, ...]:
    out = list(a)
    for x in b:
        if x not in out:
            out.append(x)
    return tuple(out)


__all__ = ["UNREADABLE", "DependencyPlanner", "PathResult"]
