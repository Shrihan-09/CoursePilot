"""Read-only composition of independently versioned major and school rules.

School rules attach through explicit school + catalog-year applicability. We do
not clone school trees per major or infer graduation eligibility from coverage.
"""

from sqlalchemy import select

from app.models import Requirement, SchoolRequirementSetVersion


def school_requirement_sets(session, version):
    return list(
        session.scalars(
            select(SchoolRequirementSetVersion)
            .where(
                SchoolRequirementSetVersion.school_id == version.program.school_id,
                SchoolRequirementSetVersion.catalog_year == version.catalog_year,
            )
            .order_by(SchoolRequirementSetVersion.code)
        )
    )


def composed_requirements(session, version):
    sets = school_requirement_sets(session, version)
    local = list(
        session.scalars(select(Requirement).where(Requirement.program_version_id == version.id))
    )
    if sets:
        shared = list(
            session.scalars(
                select(Requirement).where(
                    Requirement.school_requirement_set_version_id.in_([s.id for s in sets])
                )
            )
        )
        # Legacy embedded rows remain stored and reversible. Independent rows
        # supersede only the same explicitly represented display systems.
        systems = {r.requirement_system for r in shared}
        local = [r for r in local if r.requirement_system not in systems]
        local.extend(shared)
    codes = [r.code for r in local]
    if len(codes) != len(set(codes)):
        raise ValueError("Composed requirement codes collide; context must be disambiguated")
    return sorted(local, key=lambda r: (r.sort_order, r.code))


def composed_requirement_ids(session, version):
    return [r.id for r in composed_requirements(session, version)]


def context_coverage(session, version):
    sets = school_requirement_sets(session, version)
    return {
        "scope": "modeled_requirements",
        "complete": False,
        "school_context": "available" if sets else "missing",
        "requirement_sets": [
            {
                "id": str(s.id),
                "code": s.code,
                "name": s.name,
                "catalog_year": s.catalog_year,
                "definition_version": s.definition_version,
                "lifecycle_state": s.lifecycle_state,
                "source_url": s.source_url,
            }
            for s in sets
        ],
        "missing_scopes": ["graduation_credits", "residency", "additional_programs"]
        + ([] if sets else ["school_requirements"]),
    }


def allocation_scope(requirement):
    return requirement.allocation_scope or requirement.requirement_system


def attempt_eligibility(requirement, attempt, course, evidence=()):
    """Tri-state policy; degree credit does not imply eligibility for every set.

    A generic Rutgers origin is insufficient proof of a particular campus.
    Missing evidence remains provisional, rather than silently granting it.
    """
    from app.domain.grades import Tri, is_letter

    origin = attempt.credit_origin or "rutgers"
    if requirement.eligible_origins and origin not in requirement.eligible_origins:
        return Tri.NO, "Credit origin is not permitted for this requirement."
    if requirement.graded_only and attempt.status == "completed":
        if origin == "rutgers" and attempt.grade in ("P", "PA", "NC", "S", "U"):
            return Tri.NO, "A graded credit-bearing course is required."
        if origin != "rutgers" or not is_letter(attempt.grade):
            return Tri.UNKNOWN, "Graded-course eligibility needs supporting evidence."
    if requirement.required_campus:
        # Course is campus-independent, and StudentCourse has no certified
        # attendance-campus field. Never substitute catalog/SOC availability.
        campuses = {getattr(attempt, "attendance_campus", None)} - {None}
        for claim in evidence:
            if (
                claim.verified
                and claim.evidence_kind == "attendance_campus"
                and claim.evidence_key == course.course_string
                and claim.value.get("term_code") == attempt.term_code
                and claim.value.get("campus")
            ):
                campuses.add(claim.value["campus"])
        if len(campuses) > 1:
            return Tri.UNKNOWN, "Verified attendance-campus claims conflict."
        campus = next(iter(campuses), None)
        if campus is None:
            return Tri.UNKNOWN, "Attendance at the required campus is unverified."
        if campus != requirement.required_campus:
            return Tri.NO, "Attendance campus does not meet this requirement."
    return Tri.YES, "Requirement-specific attempt eligibility is established."


def academic_evidence(session, student):
    from app.models import StudentAcademicEvidence

    return list(
        session.scalars(
            select(StudentAcademicEvidence)
            .where(StudentAcademicEvidence.student_id == student.id)
            .order_by(
                StudentAcademicEvidence.evidence_kind,
                StudentAcademicEvidence.evidence_key,
                StudentAcademicEvidence.id,
            )
        )
    )


def requirement_exemptions(requirements, evidence):
    """Only explicitly verified source-backed evidence can meet a waiver rule."""
    from decimal import Decimal, InvalidOperation

    exempt = set()
    for req in requirements:
        rule = req.exemption_rule or {}
        if not rule:
            continue
        for claim in evidence:
            if (
                not claim.verified
                or claim.evidence_kind != rule.get("evidence_kind")
                or claim.evidence_key not in rule.get("keys", [])
            ):
                continue
            try:
                value = Decimal(str(claim.value.get("score")))
                minimum = Decimal(str(rule["minimum_score"]))
            except (InvalidOperation, KeyError):
                continue
            if not value.is_finite() or not minimum.is_finite():
                continue
            allowed = rule.get("allowed_scores")
            if allowed is not None and value not in [Decimal(str(v)) for v in allowed]:
                continue
            if value >= minimum:
                exempt.add(req.code)
    return exempt
