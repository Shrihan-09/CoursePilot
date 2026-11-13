"""Loader for requirement COMPONENTS (Prompt 08 B1).

A component is a requirement tree owned by a school and encoded once:

    gen_ed        SEBS Core, SOE H/SS, the Rutgers Core as RBS states it
    school_core   RBS business core, SoE first-year program (004)
    entry_stage   what comes before a stage (RBS foundations -> admission)
    shared_major  one major offered by two schools

Definition files live in data/programs/rutgers/<campus>/<year>/components/ and
follow the multi-source encoding format (docs/ENCODING_MAJORS.md): every node
quotes its source capture(s) by id from data/programs/rutgers/sources.json.

Same rules as the program loader: never invent a course (unresolvable codes
are reported, never created), idempotent, and a changed definition resets
review (program_lifecycle.school_set_definition_changed).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from app.models import (
    CurationStatus,
    DataSource,
    ProgramRule,
    Requirement,
    School,
    SchoolRequirementSetVersion,
)
from app.models.requirements import ComponentKind
from app.services.academic_context import APPLICABILITY_KEYS
from app.services.eligibility import reconcile, rule_from_definition
from sqlalchemy import select
from sqlalchemy.orm import Session

from coursepilot_ingestion.loaders.requirements import _grade_semantics

KINDS = {k.value for k in ComponentKind}
#: Rule types a school rule may use; the new policy types must be stored as
#: not evaluable (the audit then reports NOT_EVALUABLE, never "met").
RULE_TYPES = {"max_grade_count", "course_exclusion", "residency", "min_gpa",
              "residency_window", "pass_fail_limit", "double_count_limit"}
UNEVALUATED_TYPES = {"residency_window", "pass_fail_limit", "double_count_limit"}
RULE_TYPES = RULE_TYPES | {"no_major_credit"}
#: Top-level keys that carry requirement meaning (what a reviewer approves).
SEMANTIC_KEYS = ("component", "requirements", "school_rules")


@dataclass(slots=True)
class ComponentLoadStats:
    code: str = ""
    inserted: bool = False
    definition_changed: bool = False
    requirements_inserted: int = 0
    requirements_updated: int = 0
    requirements_removed: int = 0
    eligibility_inserted: int = 0
    eligibility_removed: int = 0
    rules: int = 0
    unresolved_courses: list[str] = field(default_factory=list)


def component_sha256(definition: dict) -> str:
    semantic = {k: definition.get(k) for k in SEMANTIC_KEYS}
    canonical = json.dumps(semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def node_fields(rdef: dict) -> dict:
    """Requirement columns a node definition sets (shared with the program loader)."""
    def dec(v):
        return None if v is None else Decimal(str(v))

    caps = rdef.get("group_caps")
    for cap in caps or ():
        if not cap.get("courses") or int(cap.get("max", -1)) < 0:
            raise ValueError(f"{rdef['code']}: group_caps entries need courses and max >= 0")
    return dict(
        name=rdef["name"],
        requirement_type=rdef["requirement_type"],
        sort_order=rdef.get("sort_order", 0),
        min_count=rdef.get("min_count"),
        min_distinct_categories=rdef.get("min_distinct_categories"),
        min_credits=dec(rdef.get("min_credits")),
        max_outside_subject=rdef.get("max_outside_subject"),
        constraint_subject_code=rdef.get("constraint_subject_code"),
        min_at_level=rdef.get("min_at_level"),
        min_at_level_count=rdef.get("min_at_level_count"),
        notes=rdef.get("notes"),
        source_prose=_prose(rdef),
        allocation_scope=rdef.get("allocation_scope"),
        eligible_origins=rdef.get("eligible_origins"),
        graded_only=rdef.get("graded_only", False),
        required_campus=rdef.get("required_campus"),
        exemption_rule=rdef.get("exemption_rule"),
        available_after_stage=rdef.get("available_after_stage"),
        min_course_credits=dec(rdef.get("min_course_credits")),
        certification_codes=sorted(set(rdef["certification_codes"]))
        if rdef.get("certification_codes") else None,
        group_caps=caps or None,
        unresolved_conflicts=sorted(set(rdef["unresolved_conflicts"]))
        if rdef.get("unresolved_conflicts") else None,
        review_flags=rdef.get("review_flags") or None,
        **_grade_semantics(rdef),
    )


def _prose(rdef: dict) -> str | None:
    """A node's quoted prose: `source_prose`, or its quotes joined (multi-source)."""
    if rdef.get("source_prose"):
        return rdef["source_prose"]
    quotes = [q["quote"] for q in rdef.get("quotes", []) if q.get("quote")]
    return " ... ".join(quotes) or None


class ComponentLoader:
    def __init__(self, session: Session) -> None:
        self.session = session

    def load_file(self, path: pathlib.Path) -> ComponentLoadStats:
        raw = path.read_bytes()
        return self.load(json.loads(raw), raw)

    def _source(self, definition: dict, raw: bytes) -> DataSource:
        digest = hashlib.sha256(raw).hexdigest()
        existing = self.session.scalar(select(DataSource).where(DataSource.content_hash == digest))
        if existing is not None:
            return existing
        comp = definition["component"]
        src = DataSource(
            kind="curated_component", url=comp["source_url"],
            title=f"{comp['name']} {comp['catalog_year']}",
            retrieved_at=datetime.fromisoformat(definition["curation"]["encoded_at"]).replace(
                tzinfo=UTC),
            content_hash=digest, academic_year=comp["catalog_year"],
            record_count=len(definition.get("requirements", [])))
        self.session.add(src)
        self.session.flush()
        return src

    def load(self, definition: dict, raw: bytes) -> ComponentLoadStats:
        comp = definition["component"]
        problems = validate_component_shape(definition)
        if problems:
            raise ValueError("; ".join(problems))
        stats = ComponentLoadStats(code=comp["code"])
        source = self._source(definition, raw)
        curation = definition.get("curation", {}).get(
            "curation_status", CurationStatus.CURATED_FROM_PROSE.value)

        sdef = comp["school"]
        school = self.session.scalar(select(School).where(School.code == sdef["code"]))
        if school is None:
            school = School(code=sdef["code"], name=sdef["name"],
                            campus_code=sdef.get("campus_code"), source_id=source.id)
            self.session.add(school)
            self.session.flush()

        digest = component_sha256(definition)
        version = self.session.scalar(select(SchoolRequirementSetVersion).where(
            SchoolRequirementSetVersion.school_id == school.id,
            SchoolRequirementSetVersion.catalog_year == comp["catalog_year"],
            SchoolRequirementSetVersion.code == comp["code"]))
        if version is None:
            version = SchoolRequirementSetVersion(
                school_id=school.id, catalog_year=comp["catalog_year"], code=comp["code"],
                name=comp["name"], definition_sha256=digest, lifecycle_state="pending_review",
                source_url=comp["source_url"], source_id=source.id)
            self.session.add(version)
            stats.inserted = True
        elif version.definition_sha256 != digest:
            from app.services.program_lifecycle import school_set_definition_changed

            school_set_definition_changed(version, digest)
            stats.definition_changed = True
        version.name = comp["name"]
        version.source_url = comp["source_url"]
        version.source_id = source.id
        version.definition_version = str(comp.get("definition_version", "1"))
        version.sharing_policy = comp.get("sharing_policy", "exclusive")
        version.kind = comp["kind"]
        version.composition = comp.get("composition") or (
            "school" if comp["kind"] == ComponentKind.GEN_ED.value else "linked")
        version.applicability = comp.get("applicability")
        version.stage = comp.get("stage")
        total = comp.get("degree_credits")
        version.degree_credits_min = Decimal(str(total["credits"])) if total else None
        version.degree_credits_source = _total_source(total, definition) if total else None
        self.session.flush()

        system = comp.get("requirement_system", "core" if comp["kind"] == "gen_ed" else "major")
        by_code: dict[str, Requirement] = {}
        roots = [r for r in definition["requirements"] if not r.get("parent")]
        for rdef in definition["requirements"]:
            fields = node_fields(rdef)
            if comp["kind"] == ComponentKind.ENTRY_STAGE.value and rdef in roots:
                # A student VERIFIED as past the stage no longer needs its entry
                # requirements (academic_context.requirement_exemptions).
                fields["exemption_rule"] = {"evidence_kind": "admission",
                                            "keys": [comp["stage"]["code"]]}
            req = self.session.scalar(select(Requirement).where(
                Requirement.school_requirement_set_version_id == version.id,
                Requirement.code == rdef["code"]))
            if req is None:
                req = Requirement(school_requirement_set_version_id=version.id, code=rdef["code"],
                                  source_id=source.id, requirement_system=system,
                                  curation_status=curation, **fields)
                self.session.add(req)
                stats.requirements_inserted += 1
            else:
                for attr, value in fields.items():
                    setattr(req, attr, value)
                req.requirement_system = system
                req.curation_status = curation
                req.source_id = source.id
                stats.requirements_updated += 1
            by_code[rdef["code"]] = req
        self.session.flush()
        for rdef in definition["requirements"]:
            parent = rdef.get("parent")
            by_code[rdef["code"]].parent_id = by_code[parent].id if parent else None
        for stale in list(self.session.scalars(select(Requirement).where(
                Requirement.school_requirement_set_version_id == version.id,
                Requirement.code.not_in(list(by_code))))):
            self.session.delete(stale)
            stats.requirements_removed += 1
        self.session.flush()

        # Eligibility: each node's rule is stored, then reconciled. A node with
        # no rule keeps an EMPTY rule (not NULL) so stale rows are removed.
        for rdef in definition["requirements"]:
            req = by_code[rdef["code"]]
            rule = rule_from_definition(rdef)
            req.eligibility_rule = rule if rule is not None else {}
        self.session.flush()
        report = reconcile(self.session, [r.id for r in by_code.values()])
        stats.eligibility_inserted, stats.eligibility_removed = report.inserted, report.deleted
        stats.unresolved_courses = report.unresolved

        stated = set()
        for rdef in definition.get("school_rules", []):
            stated.add(rdef["code"])
            values = {
                "name": rdef["name"], "rule_type": rdef["rule_type"], "grade": rdef.get("grade"),
                "max_count": rdef.get("max_count"), "min_count": rdef.get("min_count"),
                "subject_code": rdef.get("subject_code"),
                "offering_unit_code": rdef.get("offering_unit_code"),
                "excluded_course_strings": rdef.get("excluded_course_strings"),
                "min_gpa": Decimal(str(rdef["min_gpa"])) if rdef.get("min_gpa") is not None else None,
                "gpa_scope": rdef.get("gpa_scope"), "is_evaluable": rdef.get("is_evaluable", True),
                "not_evaluable_reason": rdef.get("not_evaluable_reason"), "source_prose": _prose(rdef),
                "curation_status": curation}
            rule = self.session.scalar(select(ProgramRule).where(
                ProgramRule.school_requirement_set_version_id == version.id,
                ProgramRule.code == rdef["code"]))
            if rule is None:
                self.session.add(ProgramRule(school_requirement_set_version_id=version.id,
                                             code=rdef["code"], source_id=source.id, **values))
            else:
                for attr, value in values.items():
                    setattr(rule, attr, value)
            stats.rules += 1
        for stale in list(self.session.scalars(select(ProgramRule).where(
                ProgramRule.school_requirement_set_version_id == version.id))):
            if stale.code not in stated:
                self.session.delete(stale)
        self.session.flush()
        return stats


def _total_source(total: dict, definition: dict) -> dict:
    from coursepilot_ingestion.encoding.sources import load_manifest

    capture = load_manifest().get(total.get("capture"), {})
    return {"title": total.get("title", "Degree credit total"), "quote": total.get("quote", ""),
            "url": capture.get("final_url") or capture.get("requested_url", ""),
            "retrieved_at": capture.get("retrieved_at", ""),
            "archive": capture.get("archive_path", ""),
            "archive_sha256": capture.get("sha256", ""), "capture": total.get("capture")}


def validate_component_shape(definition: dict) -> list[str]:
    """Structural checks the loader refuses to load without (validation.py
    adds the quote checks against the archived sources)."""
    errors = []
    comp = definition.get("component") or {}
    for key in ("code", "name", "kind", "school", "catalog_year", "source_url"):
        if key not in comp:
            errors.append(f"component.{key} missing")
    if errors:
        return errors
    if comp["kind"] not in KINDS:
        errors.append(f"unknown component kind {comp['kind']!r}")
    if comp.get("composition") not in (None, "school", "linked"):
        errors.append(f"unknown composition {comp.get('composition')!r}")
    if comp["kind"] == ComponentKind.ENTRY_STAGE.value:
        stage = comp.get("stage") or {}
        if not stage.get("code") or not stage.get("label"):
            errors.append("an entry_stage component needs stage.code and stage.label")
    unknown = set(comp.get("applicability") or {}) - APPLICABILITY_KEYS
    if unknown:
        errors.append(f"unknown applicability keys {sorted(unknown)}")
    if "curation" not in definition or "encoded_at" not in definition["curation"]:
        errors.append("curation.encoded_at missing")
    reqs = definition.get("requirements", [])
    codes = [r["code"] for r in reqs]
    if len(codes) != len(set(codes)):
        errors.append("duplicate requirement codes")
    if reqs and len([r for r in reqs if not r.get("parent")]) != 1:
        errors.append("expected exactly one root requirement")
    for r in reqs:
        if r.get("parent") and r["parent"] not in codes:
            errors.append(f"{r['code']}: parent {r['parent']!r} does not exist")
        if r["requirement_type"] == "choose_n" and not r.get("min_count"):
            errors.append(f"{r['code']}: choose_n without min_count")
    for rule in definition.get("school_rules", []):
        if rule.get("rule_type") not in RULE_TYPES:
            errors.append(f"{rule.get('code')}: unknown rule_type {rule.get('rule_type')!r}")
        if rule.get("rule_type") in UNEVALUATED_TYPES and rule.get("is_evaluable", True):
            errors.append(f"{rule['code']}: {rule['rule_type']} is not evaluated yet; "
                          "set is_evaluable=false with a reason")
        if not rule.get("is_evaluable", True) and not rule.get("not_evaluable_reason"):
            errors.append(f"{rule['code']}: a non-evaluable rule must say why")
    return errors


__all__ = ["ComponentLoadStats", "ComponentLoader", "component_sha256", "node_fields",
           "validate_component_shape"]
