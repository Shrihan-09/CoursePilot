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

from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.attempts import Attempt
from app.domain.audit import CourseRef, RequirementStatus, RuleResult
from app.domain.gpa import UNSUPPORTED_SCOPES, evaluate_min_gpa
from app.domain.grades import OutcomeKind, Tri, outcome
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


@dataclass(slots=True)
class RuleContext:
    """What the Degree Engine knows AFTER allocation (Phase 6.4).

    `applied_to_major`: the courses allocated to the program's major-system
    requirements, one per course identity, by the attempt that counts.
    `attempts` / `credits_of`: the full attempt history, for GPA rules.
    """

    applied_to_major: list[StudentCourseView] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    credits_of: dict = field(default_factory=dict)


def evaluate_rule(rule: ProgramRule, courses: list[StudentCourseView],
                  context: RuleContext | None = None) -> RuleResult:
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
        if context is not None:
            return _eval_max_grade_count_applied(rule, context, base)
        return _eval_max_grade_count(rule, courses, base)
    if rule.rule_type == ProgramRuleType.MIN_GPA.value:
        return _eval_min_gpa(rule, context or RuleContext(), base)
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


def _is_grade(view: StudentCourseView, target: str) -> Tri:
    """Is this course's counted grade exactly `target` (e.g. "a grade of D")?"""
    o = outcome(view.status, view.grade)
    if o.kind is OutcomeKind.LETTER:
        return Tri.YES if o.letter == target else Tri.NO
    if o.kind is OutcomeKind.PASS:                       # one of A..C
        return Tri.NO if target in ("D", "F") else Tri.UNKNOWN
    if o.kind is OutcomeKind.NO_CREDIT:                  # D or F - and earns nothing
        return Tri.NO
    if o.kind is OutcomeKind.IN_PROGRESS:
        return Tri.UNKNOWN
    return Tri.UNKNOWN


def _eval_max_grade_count_applied(rule: ProgramRule, context: RuleContext,
                                  base: dict) -> RuleResult:
    """"No more than one grade of D can be accepted in the courses required
    for the major" (CS) / "No more than one D grade can be applied toward the
    major" (Philosophy) - Phase 6.4 scope.

    Counted over the courses ALLOCATED to the major's requirements, each
    course once by the attempt that counts. Before 6.4 the whole record was
    counted, so a D in a course outside the major, or the earlier D of a
    retaken course, wrongly counted against the student.

    Three-valued: a grade that could be D (in progress, temporary, Pass
    against a D ceiling is NOT one - Pass means A..C) leaves the verdict
    NOT_EVALUABLE when it could push the count over the limit.
    """
    target = (rule.grade or "").strip().upper()
    allowed = rule.max_count if rule.max_count is not None else 0
    known = [v for v in context.applied_to_major if _is_grade(v, target) is Tri.YES]
    maybe = [v for v in context.applied_to_major if _is_grade(v, target) is Tri.UNKNOWN]
    evidence = {"scope": "courses applied to the major",
                "counted": sorted(v.course_string for v in known),
                "undetermined": sorted(v.course_string for v in maybe)}
    if len(known) > allowed:
        status = RequirementStatus.UNSATISFIED
        reason = (f"{len(known)} courses applied to the major have a grade of {target}; at most "
                  f"{allowed} may count. Affected: {', '.join(evidence['counted'])}.")
    elif len(known) + len(maybe) > allowed:
        status = RequirementStatus.NOT_EVALUABLE
        reason = (f"{len(known)} grade(s) of {target} counted; {len(maybe)} more course(s) have "
                  f"no final comparable grade yet and could exceed the limit of {allowed}.")
    else:
        status = RequirementStatus.SATISFIED
        reason = (f"{len(known)} grade(s) of {target} among the courses applied to the major; "
                  f"at most {allowed} permitted.")
    return RuleResult(**base, status=status, observed_count=len(known), allowed_count=allowed,
                      affected_courses=[v.ref for v in known], evidence=evidence, reason=reason)


def _eval_min_gpa(rule: ProgramRule, context: RuleContext, base: dict) -> RuleResult:
    """A minimum GPA in a stated scope (app.domain.gpa). Never guessed: an
    undefined scope ("in the major") or an incomplete record is NOT_EVALUABLE,
    with whatever could be computed kept as evidence."""
    scope = rule.gpa_scope or "cumulative"
    threshold = rule.min_gpa
    if threshold is None:
        return RuleResult(**base, status=RequirementStatus.INDETERMINATE,
                          reason="GPA rule has no threshold.")
    if scope in UNSUPPORTED_SCOPES:
        return RuleResult(**base, status=RequirementStatus.NOT_EVALUABLE,
                          evidence={"scope": scope, "threshold": str(threshold)},
                          reason=f"GPA scope '{scope}' cannot be computed: "
                                 f"{UNSUPPORTED_SCOPES[scope]}.")
    verdict = evaluate_min_gpa(context.attempts, context.credits_of, threshold, scope)
    status = {Tri.YES: RequirementStatus.SATISFIED, Tri.NO: RequirementStatus.UNSATISFIED,
              Tri.UNKNOWN: RequirementStatus.NOT_EVALUABLE}[verdict.status]
    g = verdict.gpa
    return RuleResult(
        **base, status=status,
        evidence={"scope": scope, "threshold": str(threshold),
                  "computed_gpa": str(g.value) if g.value is not None else None,
                  "credits": str(g.credits), "grade_points": str(g.grade_points),
                  "included": g.included, "excluded": g.excluded,
                  "unknown_reasons": g.unknown_reasons},
        reason=verdict.reason)


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
