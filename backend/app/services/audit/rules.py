"""Program-level rule evaluation.

These are constraints on the DEGREE rather than on one requirement, so they
are evaluated separately from the requirement tree and then folded into the
overall verdict.

All three come from real clauses in the Rutgers CS prose. Each is evaluated
only to the extent the source and the available student data actually support
- a rule we cannot check reports NOT_EVALUABLE rather than quietly passing.

Pure functions over data. No LLM, no network, no database access.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.audit import CourseRef, RequirementStatus, RuleResult
from app.models import ProgramRule, ProgramRuleType


@dataclass(slots=True)
class StudentCourseView:
    """The subset of a student's course record the rules need.

    A plain dataclass rather than an ORM object so rule evaluation stays a
    pure function and can be unit-tested without a database.
    """

    ref: CourseRef
    course_string: str
    subject_code: str
    offering_unit_code: str
    status: str
    grade: str | None
    credits: Decimal | None


def evaluate_rule(rule: ProgramRule, courses: list[StudentCourseView]) -> RuleResult:
    """Evaluate one program rule against a student's record."""
    base = {
        "rule_code": rule.code,
        "rule_name": rule.name,
        "rule_type": rule.rule_type,
        "source_prose": rule.source_prose,
        "curation_status": rule.curation_status,
    }

    # Honesty gate, checked before any rule-specific logic: an authoritative
    # rule we cannot check must never be reported as satisfied.
    if not rule.is_evaluable:
        return RuleResult(
            **base,
            status=RequirementStatus.NOT_EVALUABLE,
            reason=rule.not_evaluable_reason
            or "This rule is authoritative but cannot be evaluated with the available data.",
        )

    if rule.rule_type == ProgramRuleType.MAX_GRADE_COUNT.value:
        return _eval_max_grade_count(rule, courses, base)
    if rule.rule_type == ProgramRuleType.COURSE_EXCLUSION.value:
        return _eval_course_exclusion(rule, courses, base)
    if rule.rule_type == ProgramRuleType.RESIDENCY.value:
        return _eval_residency(rule, courses, base)

    return RuleResult(
        **base,
        status=RequirementStatus.INDETERMINATE,
        reason=f"Unknown rule type {rule.rule_type!r}; cannot evaluate.",
    )


def _eval_max_grade_count(rule: ProgramRule, courses: list[StudentCourseView], base: dict) -> RuleResult:
    """"No more than one grade of D can be accepted in the courses required
    for the major."

    Scope note: the prose says "courses required for the major". We evaluate
    over the student's completed courses rather than only over allocated ones,
    because a D in a required course counts whether or not the allocator
    happened to use it. Where the distinction matters it is stated in the
    reason, not silently resolved.
    """
    target = (rule.grade or "").strip().upper()
    allowed = rule.max_count if rule.max_count is not None else 0

    matching = [
        c
        for c in courses
        if c.status == "completed" and (c.grade or "").strip().upper() == target
    ]
    count = len(matching)

    if count <= allowed:
        status = RequirementStatus.SATISFIED
        reason = (
            f"{count} grade(s) of {target} on record; at most {allowed} permitted."
        )
    else:
        status = RequirementStatus.UNSATISFIED
        reason = (
            f"{count} grades of {target} on record, but at most {allowed} is permitted. "
            f"Affected: {', '.join(sorted(c.course_string for c in matching))}."
        )

    return RuleResult(
        **base,
        status=status,
        observed_count=count,
        allowed_count=allowed,
        affected_courses=[c.ref for c in matching],
        reason=reason,
    )


def _eval_course_exclusion(rule: ProgramRule, courses: list[StudentCourseView], base: dict) -> RuleResult:
    """"Declared computer science majors will not receive credit for ... 105,
    107, 110, 142, 170, or 405."

    Note what this does NOT do: it does not mark the Course invalid, and it
    does not delete anything. The courses exist academically and may count
    toward another program entirely. The exclusion is a property of THIS
    program version's relationship to those courses.

    A rule with matches is still SATISFIED - it is not a violation to have
    taken an excluded course, it simply earns no credit here.
    """
    excluded = set(rule.excluded_courses)
    matching = [
        c for c in courses if c.course_string in excluded and c.status in ("completed", "in_progress")
    ]

    if not matching:
        reason = (
            f"No excluded courses on record ({len(excluded)} course(s) earn no credit "
            "toward this program)."
        )
    else:
        names = ", ".join(sorted(c.course_string for c in matching))
        reason = (
            f"{len(matching)} course(s) on record earn no credit toward this program: {names}. "
            "They remain valid courses and may count toward another program."
        )

    return RuleResult(
        **base,
        # Always satisfied: this rule describes how credit is counted, not a
        # condition the student must meet.
        status=RequirementStatus.SATISFIED,
        observed_count=len(matching),
        affected_courses=[c.ref for c in matching],
        reason=reason,
    )


def _eval_residency(rule: ProgramRule, courses: list[StudentCourseView], base: dict) -> RuleResult:
    """Reached only when the curator marked residency evaluable.

    For the Rutgers CS rule it is NOT - see the fixture's
    `not_evaluable_reason` - so this path exists for a future program whose
    residency rule the data can actually support.

    Even here the count is "courses matching the department code", which is a
    weaker claim than "courses taken at Rutgers-New Brunswick". The reason
    text says so rather than implying more than we know.
    """
    needed = rule.min_count or 0
    subject = rule.subject_code
    unit = rule.offering_unit_code

    matching = [
        c
        for c in courses
        if c.status == "completed"
        and (subject is None or c.subject_code == subject)
        and (unit is None or c.offering_unit_code == unit)
    ]
    count = len(matching)

    if count >= needed:
        status = RequirementStatus.SATISFIED
    elif count:
        status = RequirementStatus.PARTIALLY_SATISFIED
    else:
        status = RequirementStatus.UNSATISFIED

    return RuleResult(
        **base,
        status=status,
        observed_count=count,
        allowed_count=needed,
        affected_courses=[c.ref for c in matching],
        reason=(
            f"{count} of {needed} course(s) carry the required department code. "
            "This counts department codes, not the campus a course was taken at."
        ),
    )


def excluded_course_strings(rules: list[ProgramRule]) -> set[str]:
    """Every course code excluded by any evaluable exclusion rule.

    Used to split completed credits from degree-applicable credits.
    """
    out: set[str] = set()
    for rule in rules:
        if rule.rule_type == ProgramRuleType.COURSE_EXCLUSION.value and rule.is_evaluable:
            out.update(rule.excluded_courses)
    return out
