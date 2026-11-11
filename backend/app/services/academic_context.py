"""Read-only composition of independently versioned major and school rules.

School rules attach through explicit school + catalog-year applicability. We do
not clone school trees per major or infer graduation eligibility from coverage.

Prompt 08 B1 adds COMPONENTS (`SchoolRequirementSetVersion.kind/composition`):

  * composition "school" - gen-ed, composed with every program of its school
    and catalog year (the original behaviour, unchanged);
  * composition "linked" - shared school cores (RBS business core, SoE
    first-year 004), entry stages (pre-business -> RBS) and shared majors
    (Biological Sciences, SAS and SEBS), composed ONLY with program versions
    that link them in `program_version_component`.

A shared major is authored once and linked to each school's program version;
each composes its OWN school's gen-ed, because gen-ed attaches by the program
version's school - never by the component's owner.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import Requirement, SchoolRequirementSetVersion
from app.models.requirements import ComponentKind, ProgramVersionComponent

#: Applicability keys a component may state. An unknown key makes the loader
#: refuse the definition (coursepilot_ingestion.loaders.components) - a key
#: nothing evaluates would silently apply a rule to everyone.
APPLICABILITY_KEYS = frozenset({"matriculated_from_term", "matriculated_before_term",
                                "entered_school_from_term",
                                "curriculum_class_years", "note"})
#: Verified admission statuses that pass a stage.
PASSED = frozenset({"admitted", "declared"})


def _school_sets(session, version):
    return list(session.scalars(
        select(SchoolRequirementSetVersion).where(
            SchoolRequirementSetVersion.school_id == version.program.school_id,
            SchoolRequirementSetVersion.catalog_year == version.catalog_year,
            SchoolRequirementSetVersion.composition == "school",
        ).order_by(SchoolRequirementSetVersion.code)))


def linked_components(session, version):
    return list(session.scalars(
        select(SchoolRequirementSetVersion)
        .join(ProgramVersionComponent,
              ProgramVersionComponent.component_id == SchoolRequirementSetVersion.id)
        .where(ProgramVersionComponent.program_version_id == version.id)
        .order_by(ProgramVersionComponent.sort_order, SchoolRequirementSetVersion.code)))


def school_requirement_sets(session, version):
    """Every component composed with `version`: school gen-ed, then linked ones."""
    seen, out = set(), []
    for s in _school_sets(session, version) + linked_components(session, version):
        if s.id not in seen:
            seen.add(s.id)
            out.append(s)
    return out


def composed_requirements(session, version):
    school = _school_sets(session, version)
    linked = [s for s in linked_components(session, version) if s.id not in {x.id for x in school}]
    local = list(
        session.scalars(select(Requirement).where(Requirement.program_version_id == version.id))
    )
    if school:
        shared = list(session.scalars(select(Requirement).where(
            Requirement.school_requirement_set_version_id.in_([s.id for s in school]))))
        # Legacy embedded rows remain stored and reversible. Independent rows
        # supersede only the same explicitly represented display systems -
        # school gen-ed only: a LINKED major component must never displace the
        # program's own major nodes.
        systems = {r.requirement_system for r in shared}
        local = [r for r in local if r.requirement_system not in systems]
        local.extend(shared)
    if linked:
        local.extend(session.scalars(select(Requirement).where(
            Requirement.school_requirement_set_version_id.in_([s.id for s in linked]))))
    codes = [r.code for r in local]
    if len(codes) != len(set(codes)):
        raise ValueError("Composed requirement codes collide; context must be disambiguated")
    return sorted(local, key=lambda r: (r.sort_order, r.code))


def composed_rules(session, version):
    """The program's own rules plus the school-level rules of every composed
    component (Prompt 08 B1): a school rule is encoded once, on its component."""
    from app.models import ProgramRule

    sets = [s.id for s in school_requirement_sets(session, version)]
    cond = ProgramRule.program_version_id == version.id
    if sets:
        cond = cond | ProgramRule.school_requirement_set_version_id.in_(sets)
    return list(session.scalars(select(ProgramRule).where(cond).order_by(ProgramRule.code)))


def composed_requirement_ids(session, version):
    return [r.id for r in composed_requirements(session, version)]


def context_coverage(session, version):
    sets = school_requirement_sets(session, version)
    has_gen_ed = any(s.kind == ComponentKind.GEN_ED.value for s in sets)
    return {
        "scope": "modeled_requirements",
        "complete": False,
        "school_context": "available" if has_gen_ed else "missing",
        "requirement_sets": [
            {
                "id": str(s.id),
                "code": s.code,
                "name": s.name,
                "kind": s.kind,
                "catalog_year": s.catalog_year,
                "definition_version": s.definition_version,
                "lifecycle_state": s.lifecycle_state,
                "source_url": s.source_url,
                "applicability": s.applicability,
            }
            for s in sets
        ],
        "missing_scopes": ["graduation_credits", "residency", "additional_programs"]
        + ([] if has_gen_ed else ["school_requirements"]),
    }


def allocation_scope(requirement):
    return requirement.allocation_scope or requirement.requirement_system


# --------------------------------------------------------------------------
# Prompt 08 B1: stages ("after admission to RBS", "after major declaration")
# --------------------------------------------------------------------------


def stage_labels(session, catalog_year: str | None = None) -> dict[str, str]:
    """stage code -> its plain label, from the ENTRY_STAGE components."""
    stmt = select(SchoolRequirementSetVersion.stage).where(
        SchoolRequirementSetVersion.kind == ComponentKind.ENTRY_STAGE.value)
    if catalog_year:
        stmt = stmt.where(SchoolRequirementSetVersion.catalog_year == catalog_year)
    out = {}
    for stage in session.scalars(stmt):
        if stage and stage.get("code"):
            out[stage["code"]] = stage.get("label") or f"after {stage['code']}"
    return out


def node_stages(requirements, version=None) -> dict[str, str | None]:
    """requirement code -> the stage its courses come after (nearest ancestor
    wins; a program's own nodes default to the program's admission stage)."""
    by_id = {r.id: r for r in requirements}
    program_stage = getattr(version, "admission_stage", None)
    out: dict[str, str | None] = {}
    for r in requirements:
        node, stage = r, None
        while node is not None:
            if node.available_after_stage:
                stage = node.available_after_stage
                break
            node = by_id.get(node.parent_id)
        if stage is None and program_stage and r.program_version_id is not None:
            stage = program_stage
        out[r.code] = stage
    return out


def passed_stages(evidence) -> set[str]:
    """Stages a VERIFIED admission/declaration record says the student passed.

    Course completion never grants admission: only an explicit verified
    record does (StudentAcademicEvidence, kind 'admission')."""
    return {c.evidence_key for c in evidence
            if c.verified and c.evidence_kind == "admission"
            and (c.value or {}).get("status") in PASSED}


def curriculum_applies(version, evidence) -> tuple[bool | None, str]:
    """Whether a class-scoped curriculum governs this student.

    True/False only from a VERIFIED class-year record; None (unknown) otherwise.
    The class year is never derived from a target graduation term: delaying
    graduation must not silently switch the governing curriculum."""
    years = version.curriculum_class_years
    if not years:
        return True, "This program has one curriculum for the catalog year."
    claims = [c for c in evidence if c.verified and c.evidence_kind == "curriculum_class_year"]
    stated = {(c.value or {}).get("class_year") for c in claims} - {None}
    if len(stated) > 1:
        return None, "Verified class-year records conflict."
    if not stated:
        return None, ("This curriculum governs specific classes "
                      f"({', '.join(str(y) for y in years)}); your class year is not confirmed.")
    year = next(iter(stated))
    if year in years:
        return True, f"Class of {year} is governed by this curriculum."
    return False, f"Class of {year} is not governed by this curriculum."


def attempt_eligibility(requirement, attempt, course, evidence=()):
    """Tri-state policy; degree credit does not imply eligibility for every set.

    A generic Rutgers origin is insufficient proof of a particular campus.
    Missing evidence remains provisional, rather than silently granting it.
    """
    from app.domain.grades import Tri, is_letter

    origin = attempt.credit_origin or "rutgers"
    if requirement.eligible_origins and origin not in requirement.eligible_origins:
        return Tri.NO, "Credit origin is not permitted for this requirement."
    minimum = getattr(requirement, "min_course_credits", None)
    if minimum is not None:
        credits = getattr(course, "credits", None)
        if credits is None:
            return Tri.UNKNOWN, f"Course credits are unknown; at least {minimum:g} are required."
        if credits < minimum:
            return Tri.NO, f"Courses must carry at least {minimum:g} credits for this requirement."
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
    """Only explicitly verified source-backed evidence can meet a waiver rule.

    Two rule shapes:
      * exam scores: {"evidence_kind": "exam_score", "keys": [...], "minimum_score": 4}
      * a passed stage (Prompt 08 B1): {"evidence_kind": "admission",
        "keys": ["rbs-nb-admission"]} - an ENTRY_STAGE component's root: a
        student verified as admitted no longer needs the entry requirements.
    """
    from decimal import Decimal, InvalidOperation

    exempt = set()
    for req in requirements:
        rule = req.exemption_rule or {}
        if not rule:
            continue
        if rule.get("evidence_kind") == "admission":
            if passed_stages(evidence) & set(rule.get("keys", [])):
                exempt.add(req.code)
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
