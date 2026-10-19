"""Read-only, exact-rational progress over Degree Engine verdicts.

Each terminal obligation has a fixed one-unit display budget. A choose-N
course obligation is one unit, not N equal-credit courses; a credit obligation
is likewise one unit. ALL_OF sums children without adding a parent unit.
ANY_OF has N units, selecting the N largest normalized alternative fractions.
These are presentation weights, never Rutgers credits, workload or eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.audit import RequirementResult, RequirementStatus
from app.domain.progress import Contribution, ExactRatio, ProgressMetric, ProgressResponse
from app.models import Course, ProgramVersion, Student, StudentCourse
from app.models.plans import StudentPlan
from app.services.audit.cache import AUDIT_ENGINE_VERSION, academic_fingerprint, rules_fingerprint
from app.services.audit.engine import DegreeAuditEngine

UNKNOWN = {RequirementStatus.INDETERMINATE, RequirementStatus.NOT_EVALUABLE}
MET = {RequirementStatus.SATISFIED, RequirementStatus.PROVISIONALLY_SATISFIED}


@dataclass(frozen=True)
class Measure:
    earned: Fraction = Fraction(0)
    budget: int = 0
    unknown: int = 0
    budget_known: bool = True


def measure(
    node: RequirementResult, *, confirmed_only=False, uncertain_codes=frozenset()
) -> Measure:
    kind = node.requirement_type
    if kind == "all_of":
        if not node.children:
            return Measure(Fraction(0), 0, 1, False)
        values = [
            measure(c, confirmed_only=confirmed_only, uncertain_codes=uncertain_codes)
            for c in node.children
        ]
        return Measure(
            sum((v.earned for v in values), Fraction(0)),
            sum(v.budget for v in values),
            sum(v.unknown for v in values),
            all(v.budget_known for v in values),
        )
    if kind == "any_of":
        need = node.needed_count or 1
        values = [
            measure(c, confirmed_only=confirmed_only, uncertain_codes=uncertain_codes)
            for c in node.children
        ]
        fractions = sorted(
            (v.earned / v.budget if v.budget else Fraction(0) for v in values), reverse=True
        )
        unknown = min(need, sum(v.unknown > 0 or v.budget == 0 for v in values))
        earned = sum(fractions[:need], Fraction(0))
        if earned == need and node.status in MET:
            unknown = 0
        return Measure(earned, need, unknown)
    if kind not in {"course", "choose_n", "credits"}:
        return Measure(Fraction(0), 0, 1, False)
    if (
        node.status in UNKNOWN
        or node.requirement_code in uncertain_codes
        or (confirmed_only and node.status is RequirementStatus.PROVISIONALLY_SATISFIED)
    ):
        return Measure(Fraction(0), 1, 1)
    if node.status in MET:
        return Measure(Fraction(1), 1, 0)
    constraints = []
    if kind == "course":
        constraints.append(Fraction(min(node.satisfied_count, 1)))
    elif node.needed_count:
        constraints.append(Fraction(node.satisfied_count, node.needed_count))
    if node.needed_credits is not None and node.needed_credits > 0:
        constraints.append(Fraction(node.satisfied_credits) / Fraction(node.needed_credits))
    if node.needed_distinct_categories:
        constraints.append(Fraction(node.distinct_categories, node.needed_distinct_categories))
    value = max(Fraction(0), min([Fraction(1), *constraints])) if constraints else Fraction(0)
    # The engine may reject a full count for a non-count constraint (level,
    # subject quota, sequence, origin). Do not manufacture 100% from its count.
    if value == 1:
        value = Fraction(0)
    return Measure(value, 1, 0)


def display_denominator(completed: Measure, current: Measure) -> int:
    """An unknown budget in either evaluation makes a scope unavailable, never a fraction."""
    return completed.budget if completed.budget_known and current.budget_known else 0


def contribution(units: Fraction, budget: int) -> Contribution:
    exact = units * 100 / budget if budget else None
    percent = (
        None
        if exact is None
        else (Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )
    return Contribution(
        units=ExactRatio(numerator=units.numerator, denominator=units.denominator),
        percent=percent,
        exact_percent=None
        if exact is None
        else ExactRatio(numerator=exact.numerator, denominator=exact.denominator),
    )


def _scope_measure(
    nodes, systems, wanted=None, *, confirmed_only=False, uncertain_codes=frozenset()
):
    chosen = []
    for node in nodes:
        if node.children:
            if node.requirement_type == "any_of":
                leaf_systems = {
                    systems.get(n.requirement_code) for n in _walk(node.children) if not n.children
                }
                if wanted is None or leaf_systems <= wanted:
                    chosen.append(
                        measure(
                            node, confirmed_only=confirmed_only, uncertain_codes=uncertain_codes
                        )
                    )
                elif leaf_systems & wanted:
                    # A choice between scopes cannot safely become two metrics.
                    need = node.needed_count or 1
                    chosen.append(Measure(Fraction(0), need, need, False))
            else:
                chosen.append(
                    _scope_measure(
                        node.children,
                        systems,
                        wanted,
                        confirmed_only=confirmed_only,
                        uncertain_codes=uncertain_codes,
                    )
                )
        elif wanted is None or systems.get(node.requirement_code) in wanted:
            chosen.append(
                measure(node, confirmed_only=confirmed_only, uncertain_codes=uncertain_codes)
            )
    return Measure(
        sum((v.earned for v in chosen), Fraction(0)),
        sum(v.budget for v in chosen),
        sum(v.unknown for v in chosen),
        all(v.budget_known for v in chosen),
    )


def _scope_nodes(nodes, systems, wanted=None):
    """Preserve logical grouping and reasons for inspection, without mutation."""
    result = []
    for node in nodes:
        if not node.children:
            if wanted is None or systems.get(node.requirement_code) in wanted:
                result.append(node)
            continue
        children = _scope_nodes(node.children, systems, wanted)
        if children:
            if node.requirement_type == "any_of":
                result.append(node)
            else:
                result.append(node.model_copy(update={"children": children}))
    return result


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.children)


def build_progress(session: Session, student: Student) -> ProgressResponse:
    from app.services.academic_context import composed_requirements, context_coverage
    from app.services.planning.saved import _decode, stale_reasons

    version = session.get(ProgramVersion, student.program_version_id)
    definitions = composed_requirements(session, version)
    systems = {r.code: r.requirement_system for r in definitions}
    engine = DegreeAuditEngine(session)
    baseline = engine.audit(student, statuses=frozenset({"completed"}))
    current = engine.audit(student, statuses=frozenset({"completed", "in_progress"}))
    saved = session.scalar(select(StudentPlan).where(StudentPlan.student_id == student.id))
    projected = None
    projection_state = "absent"
    if saved is not None:
        if stale_reasons(session, student, saved):
            projection_state = "stale"
        else:
            plan = _decode(saved.result_blob)
            keys = {pc.course for term in plan.terms for pc in term.courses}
            courses = {
                c.course_string: c
                for c in session.scalars(
                    select(Course).where(
                        Course.course_string.in_(keys), Course.supplement_code == ""
                    )
                )
            }
            if keys <= courses.keys():
                attempts = tuple(
                    (
                        StudentCourse(
                            student_id=student.id,
                            course_id=courses[pc.course].id,
                            term_code=term.term_code,
                            status="in_progress",
                            grade=None,
                            credits_earned=pc.credits,
                            credit_origin="rutgers",
                            source_kind="manual",
                        ),
                        courses[pc.course],
                    )
                    for term in plan.terms
                    for pc in term.courses
                )
                projected = engine.audit(
                    student, statuses=frozenset({"completed", "in_progress"}), projected=attempts
                )
                projection_state = "available"
            else:
                projection_state = "incomparable"

    def uncertainty(audit):
        return {
            f.requirement_code
            for f in audit.findings
            if f.code == "requirement_eligibility_unknown" and f.requirement_code
        } | {g.requirement_code for g in audit.grade_evaluations if g.result == "unknown"}

    metrics = []
    for scope, label, wanted in [
        ("modeled_overall", "Modeled requirements", None),
        ("primary_program", f"{baseline.program_name} {baseline.degree_type}", {"major"}),
        ("school_core", "School / Core", {"core", "school"}),
    ]:
        a = _scope_measure(
            baseline.requirements,
            systems,
            wanted,
            confirmed_only=True,
            uncertain_codes=uncertainty(baseline),
        )
        b = _scope_measure(
            current.requirements, systems, wanted, uncertain_codes=uncertainty(current)
        )
        p = (
            _scope_measure(
                projected.requirements, systems, wanted, uncertain_codes=uncertainty(projected)
            )
            if projected
            else None
        )
        comparable = a.budget == b.budget and b.earned >= a.earned
        plan_comparable = p is not None and p.budget == a.budget and p.earned >= b.earned
        if p is not None and not plan_comparable:
            projection_state = "incomparable"
        codes = [
            n.requirement_code
            for n in _walk(current.requirements)
            if not n.children and (wanted is None or systems.get(n.requirement_code) in wanted)
        ]
        denominator = display_denominator(a, b)
        metrics.append(
            ProgressMetric(
                scope=scope,
                label=label,
                availability="available" if denominator else "unavailable",
                denominator_units=a.budget if denominator else None,
                denominator_definition=(
                    "One display unit per terminal obligation; ALL_OF adds no "
                    "parent weight; ANY_OF uses its required alternative count."
                ),
                completed=contribution(a.earned, denominator),
                projected_current=contribution(b.earned, denominator),
                projected_plan=contribution(p.earned, denominator) if plan_comparable else None,
                in_progress_increment=contribution(b.earned - a.earned, denominator)
                if comparable
                else None,
                planned_increment=contribution(p.earned - b.earned, denominator)
                if plan_comparable
                else None,
                remaining=contribution(Fraction(a.budget) - a.earned, denominator),
                unknown_obligations=max(a.unknown, b.unknown),
                percentage_kind="unavailable"
                if not denominator
                else "confirmed_lower_bound"
                if a.unknown
                else "exact",
                status="unavailable"
                if not denominator
                else "unknown"
                if a.unknown
                else "modeled_complete"
                if a.earned == a.budget
                else "in_progress",
                drill_down=f"/profile#{scope}",
                requirement_codes=codes,
                requirement_results=_scope_nodes(current.requirements, systems, wanted),
                allocations=[a for a in current.allocation if a.requirement_code in codes],
                explanation=(
                    "Completed work only fills the completed segment. Current and saved-plan "
                    "projections are conditional, not completion. Rule gates and coverage "
                    "gaps remain separate."
                ),
            )
        )
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("progress must not modify academic or saved-plan state")
    return ProgressResponse(
        generated_at=datetime.now(UTC),
        audit_engine_version=AUDIT_ENGINE_VERSION,
        academic_fingerprint=academic_fingerprint(session, student),
        rules_fingerprint=rules_fingerprint(session, student.program_version_id),
        academic_context=context_coverage(session, version),
        coverage_limitations=[
            "Only attached modeled requirements are measured.",
            "Full graduation eligibility, all residency rules, and combined additional "
            "programs are not established.",
            "Presentation units are not credits or a measure of workload.",
            "Unrecorded exemptions and uncertain origin/grade evidence are not assumed.",
        ],
        projection_state=projection_state,
        projection_assumptions=[
            "In-progress and planned courses must be passed under the applicable rules.",
            "Future offerings may be historical evidence; stale plans are not projected.",
        ],
        metrics=metrics,
        gates=current.rules,
        findings=current.findings,
    )
