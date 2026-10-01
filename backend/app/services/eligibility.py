"""Requirement eligibility reconciliation (Phase 6.3).

`requirement_course_option` is DERIVED data: a curated eligibility rule
applied to the current course table. Before Phase 6.3 it was materialized
once, when a definition loaded, and only ever appended to. Two defects
followed:

  * a course first seen in a later SOC term never became eligible, even
    when it matched the rule (six real CS electives were missing after
    Phase 6.2 loaded four more terms);
  * a course removed from a curated definition stayed eligible forever.

## The rule is stored; the rows are recomputed

Each requirement a curated definition loads carries its rule in
`requirement.eligibility_rule`:

```json
{"courses": ["01:640:244", "01:640:252"],
 "course_categories": {"ANALYSIS": ["01:640:311", ...]},
 "query": {"subject_code": "640", "offering_unit_code": "01",
           "min_course_number": 300, "max_course_number": 499,
           "exclude_course_numbers": ["491", "492"]}}
```

`reconcile()` computes, for each such requirement, EXACTLY the
(course, category) pairs the rule implies over the current `course` table,
and applies the difference: missing rows are inserted, rows the rule no
longer implies are deleted, identical rows are untouched. Deterministic and
idempotent - a second run changes nothing.

Requirements WITHOUT a stored rule are never touched. SAS Core eligibility
is one: it comes from SOC `coreCodes` certifications, a different source
with its own loader, and a reconciler that did not understand it must not
delete it.

## What "current course table" means

Course IDENTITIES across every loaded SOC term - not the courses offered in
any one term. A requirement names courses; whether a course is offered next
semester is a planning question for a different layer.

## Transaction safety

The whole reconciliation runs inside a SAVEPOINT. Any failure rolls the
savepoint back, so a requirement is never left with a half-rewritten
eligibility set; the caller's transaction decides whether to commit.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Course, Requirement, RequirementCourseOption

#: Every key an eligibility query may use. Unknown keys raise: a silently
#: ignored key (an exclusion, say) would make an excluded course eligible.
QUERY_KEYS = frozenset({
    "subject_code", "offering_unit_code", "min_course_number",
    "max_course_number", "exclude_course_numbers",
})


def rule_from_definition(rdef: dict) -> dict | None:
    """The eligibility rule a curated requirement node states, or None."""
    rule = {}
    if rdef.get("courses"):
        rule["courses"] = sorted(set(rdef["courses"]))
    if rdef.get("course_categories"):
        rule["course_categories"] = {k: sorted(set(v))
                                     for k, v in sorted(rdef["course_categories"].items())}
    if rdef.get("eligible_course_query"):
        query = dict(rdef["eligible_course_query"])
        unknown = set(query) - QUERY_KEYS
        if unknown:
            raise ValueError(f"unknown eligible_course_query keys: {sorted(unknown)}")
        if "exclude_course_numbers" in query:
            query["exclude_course_numbers"] = sorted(set(query["exclude_course_numbers"]))
        rule["query"] = query
    return rule or None


def query_matches(course: Course, query: dict) -> bool:
    if "subject_code" in query and course.subject_code != query["subject_code"]:
        return False
    if "offering_unit_code" in query and course.offering_unit_code != query["offering_unit_code"]:
        return False
    if course.supplement_code != "":
        return False
    bounded = "min_course_number" in query or "max_course_number" in query
    if bounded and not course.course_number.isdigit():
        return False
    if "min_course_number" in query and int(course.course_number) < int(query["min_course_number"]):
        return False
    if "max_course_number" in query and int(course.course_number) > int(query["max_course_number"]):
        return False
    return course.course_number not in set(query.get("exclude_course_numbers", ()))


@dataclass(slots=True)
class ReconcileReport:
    requirements: int = 0
    inserted: int = 0
    deleted: int = 0
    unchanged: int = 0
    unresolved: list[str] = field(default_factory=list)


class _CourseIndex:
    """Courses loaded once per reconciliation - no per-requirement queries."""

    def __init__(self, session: Session) -> None:
        self.by_string: dict[str, Course] = {}
        self.by_subject: dict[str, list[Course]] = defaultdict(list)
        for course in session.scalars(select(Course).where(Course.supplement_code == "")):
            self.by_string.setdefault(course.course_string, course)
            self.by_subject[course.subject_code].append(course)
        self.all = [c for cs in self.by_subject.values() for c in cs]

    def query(self, query: dict) -> list[Course]:
        pool = self.by_subject.get(query["subject_code"], []) if "subject_code" in query else self.all
        return [c for c in pool if query_matches(c, query)]


def desired_pairs(rule: dict, index: _CourseIndex, code: str, report: ReconcileReport):
    pairs: set[tuple] = set()
    for course_string in rule.get("courses", ()):
        course = index.by_string.get(course_string)
        if course is None:
            report.unresolved.append(f"{code}:{course_string}")     # reported, never created
        else:
            pairs.add((course.id, ""))
    for category, strings in rule.get("course_categories", {}).items():
        for course_string in strings:
            course = index.by_string.get(course_string)
            if course is None:
                report.unresolved.append(f"{code}:{course_string}")
            else:
                pairs.add((course.id, category))
    if "query" in rule:
        pairs.update((c.id, "") for c in index.query(rule["query"]))
    return pairs


def reconcile(session: Session, requirement_ids: list | None = None) -> ReconcileReport:
    """Make every rule-bearing requirement's eligibility exactly what its rule implies."""
    report = ReconcileReport()
    stmt = select(Requirement).where(Requirement.eligibility_rule.is_not(None))
    if requirement_ids is not None:
        if not requirement_ids:
            return report
        stmt = stmt.where(Requirement.id.in_(requirement_ids))
    requirements = list(session.scalars(stmt.order_by(Requirement.code)))
    if not requirements:
        return report

    index = _CourseIndex(session)
    existing: dict = defaultdict(dict)
    for row in session.scalars(select(RequirementCourseOption).where(
            RequirementCourseOption.requirement_id.in_([r.id for r in requirements]))):
        existing[row.requirement_id][(row.course_id, row.category)] = row

    with session.begin_nested():
        for req in requirements:
            report.requirements += 1
            want = desired_pairs(req.eligibility_rule, index, req.code, report)
            have = existing.get(req.id, {})
            for pair in sorted(set(have) - want, key=str):
                session.delete(have[pair])
                report.deleted += 1
            for course_id, category in sorted(want - set(have), key=str):
                session.add(RequirementCourseOption(
                    requirement_id=req.id, course_id=course_id, category=category,
                    source_id=req.source_id))
                report.inserted += 1
            report.unchanged += len(want & set(have))
        session.flush()
    return report


__all__ = ["QUERY_KEYS", "ReconcileReport", "desired_pairs", "query_matches",
           "reconcile", "rule_from_definition"]
