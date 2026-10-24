"""Math placement (Phase 7.3) - deterministic, from published rules only.

The student profile may state the math course their placement result places
them into (`Student.math_placement`, e.g. "01:640:151"). The Rutgers-NB
Mathematics Department: "Students may not enroll in any mathematics course
unless they meet the prerequisites by suitable placement - that is, by the
placement test, an appropriate AP Calculus exam score, or with a grade of C or
better in an approved transferred course" (PLACEMENT_POLICY_URL). Placement
is therefore one way to MEET a course's prerequisites:

For a math (640) course X:

* placement P, X is P or below P - prerequisites met by placement
  (`satisfied_by_math_placement`); nothing below X is needed to reach it;
* placement P, X above or beside P - the published prerequisite decides;
* none stated, X's rule says "... OR PLACEMENT", prerequisite met by history
  or plan - met by the prerequisite, no flag;
* none stated, same rule, prerequisite unmet - UNKNOWN
  (`requires_math_placement`): the planner plans the published path, and
  places X flagged only when that path cannot be planned.

"Below P" = X appears in P's prerequisite closure under each course's most
recent published rules (placed into 01:640:151 -> 115, 112, 111, 026, ...).
No placement ladder is invented. Placement only removes prerequisites: it
never earns credit or satisfies a degree requirement. Placement conditions on
other subjects (languages, chemistry) are not math placement and are unchanged.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.prerequisites import course_keys, from_json
from app.models import Course, CoursePrerequisite

MATH_SUBJECT = "640"
REQUIRES_PLACEMENT = "requires_math_placement"
MET_BY_PLACEMENT = "satisfied_by_math_placement"
MAX_DEPTH = 8
PLACEMENT_POLICY_URL = ("https://math.sas.rutgers.edu/academics/undergraduate/"
                        "placement-advice/1061-placement-advice")


def is_math(course_key: str) -> bool:
    parts = course_key.split(":")
    return len(parts) == 3 and parts[1] == MATH_SUBJECT


def names_placement(interpreted: dict | None) -> bool:
    """Whether a prerequisite row's interpreted conditions mention placement."""
    if not interpreted:
        return False
    if "placement" in (interpreted.get("uninterpreted") or ()):
        return True
    return any("placement" in alt.get("kinds", ()) for alt in interpreted.get("alternatives") or ())


def covered(session: Session, placement: str | None) -> frozenset[str]:
    """Courses at or below `placement` (empty without one). Cached per session."""
    if not placement:
        return frozenset()
    cache = session.info.setdefault("math_placement_covered", {})
    if placement in cache:
        return cache[placement]
    seen = {placement}
    frontier = {placement}
    for _ in range(MAX_DEPTH):
        if not frontier:
            break
        latest: dict[str, tuple[str, dict | None]] = {}
        for key, term, expression in session.execute(
                select(Course.course_string, CoursePrerequisite.term_code,
                       CoursePrerequisite.expression)
                .join(Course, Course.id == CoursePrerequisite.course_id)
                .where(Course.course_string.in_(sorted(frontier)),
                       Course.supplement_code == "")):
            if key not in latest or term > latest[key][0]:
                latest[key] = (term, expression)
        below = {k for _, expr in latest.values() if expr is not None
                 for k in course_keys(from_json(expr))}
        frontier = below - seen
        seen |= below
    cache[placement] = frozenset(seen)
    return cache[placement]


__all__ = ["MET_BY_PLACEMENT", "PLACEMENT_POLICY_URL", "REQUIRES_PLACEMENT", "covered",
           "is_math", "names_placement"]
