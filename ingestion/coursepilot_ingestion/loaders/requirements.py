"""Loader for curated program requirements.

Input is a curated JSON definition (see
`data/programs/rutgers/nb-undergrad/2026-2027/sas-198-ba.json`), NOT a scraped
payload. Rutgers publishes requirements as prose, so the structure is a human
derivation and this loader's job is to persist it faithfully - including the
prose it was derived from and its curation status.

Two rules it enforces:

  * **Never invent a course.** Courses are resolved against existing `course`
    rows by the Phase 1 natural key. An unresolvable course code is reported,
    never created - a fabricated course would make a requirement satisfiable
    by something that does not exist.

  * **Idempotent.** Re-loading the same definition updates in place rather
    than duplicating, keyed on the natural keys established in Phase 3.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from app.models import (
    Course,
    CurationStatus,
    DataSource,
    Program,
    ProgramRule,
    ProgramVersion,
    Requirement,
    School,
)
from app.domain.grades import LETTER_POINTS, validate_minimum
from app.services import program_lifecycle
from app.services.eligibility import query_matches, reconcile, rule_from_definition
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RequirementLoadStats:
    schools_inserted: int = 0
    programs_inserted: int = 0
    versions_inserted: int = 0
    requirements_inserted: int = 0
    requirements_updated: int = 0
    eligibility_inserted: int = 0
    eligibility_removed: int = 0
    definition_changed: bool = False
    rules_inserted: int = 0
    rules_not_evaluable: int = 0
    unresolved_courses: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"schools+{self.schools_inserted} programs+{self.programs_inserted} "
            f"versions+{self.versions_inserted} "
            f"requirements(+{self.requirements_inserted}/~{self.requirements_updated}) "
            f"eligibility(+{self.eligibility_inserted}/-{self.eligibility_removed}) "
            f"rules+{self.rules_inserted}(not_evaluable={self.rules_not_evaluable}) "
            f"unresolved={len(self.unresolved_courses)}"
        )


class RequirementLoader:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ #
    # course resolution
    # ------------------------------------------------------------------ #

    def _resolve_course(self, course_string: str) -> Course | None:
        """Resolve 'unit:subject:number' against existing Course rows.

        Uses supplement_code='' because the catalog prose never mentions
        supplements. A lecture/lab pair would need the supplement spelled out,
        and we would rather fail to resolve than silently pick one.
        """
        try:
            unit, subject, number = course_string.split(":")
        except ValueError:
            return None
        return self.session.scalar(
            select(Course).where(
                Course.offering_unit_code == unit,
                Course.subject_code == subject,
                Course.course_number == number,
                Course.supplement_code == "",
            )
        )

    def _query_courses(self, spec: dict) -> list[Course]:
        """Resolve an `eligible_course_query` (e.g. all CS courses at 300+).

        The matching itself lives in app.services.eligibility, which is also
        what reconciliation uses - one definition of "matches", not two.
        Unknown keys raise there: an ignored exclusion would make an excluded
        course count.
        """
        rule_from_definition({"eligible_course_query": spec})       # key check
        courses = self.session.scalars(select(Course).where(Course.supplement_code == ""))
        return sorted((c for c in courses if query_matches(c, spec)),
                      key=lambda c: c.course_string)

    # ------------------------------------------------------------------ #
    # provenance
    # ------------------------------------------------------------------ #

    def _get_or_create_source(self, definition: dict, raw: bytes) -> DataSource:
        src = definition["source"]
        content_hash = hashlib.sha256(raw).hexdigest()
        existing = self.session.scalar(
            select(DataSource).where(DataSource.content_hash == content_hash)
        )
        if existing is not None:
            return existing

        source = DataSource(
            kind=src["kind"],
            url=src["url"],
            title=f"{definition['program']['name']} requirements {src['catalog_year']}",
            retrieved_at=datetime.fromisoformat(src["retrieved_at"]).replace(tzinfo=UTC),
            content_hash=content_hash,
            academic_year=src["catalog_year"],
            record_count=len(definition.get("requirements", [])),
        )
        self.session.add(source)
        self.session.flush()
        return source

    # ------------------------------------------------------------------ #
    # load
    # ------------------------------------------------------------------ #

    def load_file(self, path: pathlib.Path) -> RequirementLoadStats:
        raw = path.read_bytes()
        return self.load(json.loads(raw), raw)

    def load(self, definition: dict, raw: bytes) -> RequirementLoadStats:
        stats = RequirementLoadStats()
        source = self._get_or_create_source(definition, raw)
        curation = definition["source"].get(
            "curation_status", CurationStatus.UNVERIFIED.value
        )

        # --- school ---
        sdef = definition["school"]
        school = self.session.scalar(select(School).where(School.code == sdef["code"]))
        if school is None:
            school = School(
                code=sdef["code"],
                name=sdef["name"],
                campus_code=sdef.get("campus_code"),
                source_id=source.id,
            )
            self.session.add(school)
            self.session.flush()
            stats.schools_inserted += 1

        # --- program ---
        pdef = definition["program"]
        variant = pdef.get("variant", "")
        program = self.session.scalar(
            select(Program).where(
                Program.school_id == school.id,
                Program.code == pdef["code"],
                Program.degree_type == pdef["degree_type"],
                Program.variant == variant,
            )
        )
        if program is None:
            program = Program(
                school_id=school.id,
                code=pdef["code"],
                name=pdef["name"],
                degree_type=pdef["degree_type"],
                variant=variant,
                source_id=source.id,
            )
            self.session.add(program)
            self.session.flush()
            stats.programs_inserted += 1

        # --- version ---
        vdef = definition["program_version"]
        cdef = definition.get("curation", {})
        curriculum_key = vdef.get("curriculum_key", "")
        version = self.session.scalar(
            select(ProgramVersion).where(
                ProgramVersion.program_id == program.id,
                ProgramVersion.catalog_year == vdef["catalog_year"],
                ProgramVersion.curriculum_key == curriculum_key,
            )
        )
        if version is None:
            version = ProgramVersion(
                program_id=program.id,
                catalog_year=vdef["catalog_year"],
                curriculum_key=curriculum_key,
                # Defaults to EXCLUSIVE: a definition that says nothing about
                # sharing must not silently grant it.
                sharing_policy=vdef.get("sharing_policy", "exclusive"),
                source_url=definition["source"]["url"],
                curation_status=curation,
                source_id=source.id,
                definition_sha256=definition_sha256(definition),
            )
            self.session.add(version)
            self.session.flush()
            stats.versions_inserted += 1
        else:
            # A changed definition invalidates any validation or review of
            # the old one (program_lifecycle owns the state change).
            stats.definition_changed = program_lifecycle.definition_changed(
                version, definition_sha256(definition))
        version.total_credits_min = _dec(vdef.get("total_credits_min"))
        version.total_credits_max = _dec(vdef.get("total_credits_max"))
        version.source_prose = vdef.get("source_prose")
        version.admission_prose = vdef.get("admission_prose")
        version.curated_by = cdef.get("curated_by")
        version.extractor_version = cdef.get("extractor_version")
        # Prompt 08 B1: class-scoped curricula, the admission stage, the
        # program's own degree total.
        version.curriculum_class_years = vdef.get("curriculum_class_years")
        version.admission_stage = vdef.get("admission_stage")
        total = vdef.get("degree_credits")
        version.degree_credits_min = _dec(total["credits"]) if total else None
        if total:
            from coursepilot_ingestion.loaders.components import _total_source
            version.degree_credits_source = _total_source(total, definition)
        else:
            version.degree_credits_source = None

        # --- requirements (two passes: create, then wire parents) ---
        by_code: dict[str, Requirement] = {}
        for rdef in definition["requirements"]:
            existing = self.session.scalar(
                select(Requirement).where(
                    Requirement.program_version_id == version.id,
                    Requirement.code == rdef["code"],
                )
            )
            if existing is not None:
                # Every field the insert sets is refreshed. Before Phase 6.0
                # only four were: a recuration that changed a count, a
                # constraint or the quoted prose loaded "successfully" and
                # changed nothing, leaving a row that no longer matched its
                # own cited source.
                existing.name = rdef["name"]
                existing.requirement_type = rdef["requirement_type"]
                existing.requirement_system = rdef.get("requirement_system", "major")
                existing.sort_order = rdef.get("sort_order", 0)
                existing.min_count = rdef.get("min_count")
                existing.min_distinct_categories = rdef.get("min_distinct_categories")
                existing.min_credits = _dec(rdef.get("min_credits"))
                existing.max_outside_subject = rdef.get("max_outside_subject")
                existing.constraint_subject_code = rdef.get("constraint_subject_code")
                existing.min_at_level = rdef.get("min_at_level")
                existing.min_at_level_count = rdef.get("min_at_level_count")
                existing.notes = rdef.get("notes")
                existing.source_prose = _prose(rdef)
                existing.curation_status = curation
                for attr, value in _grade_semantics(rdef).items():
                    setattr(existing, attr, value)
                for attr, value in _b1_fields(rdef).items():
                    setattr(existing, attr, value)
                stats.requirements_updated += 1
                by_code[rdef["code"]] = existing
                continue

            req = Requirement(
                program_version_id=version.id,
                code=rdef["code"],
                name=rdef["name"],
                requirement_type=rdef["requirement_type"],
                requirement_system=rdef.get("requirement_system", "major"),
                sort_order=rdef.get("sort_order", 0),
                min_count=rdef.get("min_count"),
                min_distinct_categories=rdef.get("min_distinct_categories"),
                min_credits=_dec(rdef.get("min_credits")),
                max_outside_subject=rdef.get("max_outside_subject"),
                constraint_subject_code=rdef.get("constraint_subject_code"),
                min_at_level=rdef.get("min_at_level"),
                min_at_level_count=rdef.get("min_at_level_count"),
                notes=rdef.get("notes"),
                source_prose=_prose(rdef),
                curation_status=curation,
                source_id=source.id,
                **_grade_semantics(rdef),
                **_b1_fields(rdef),
            )
            self.session.add(req)
            self.session.flush()
            stats.requirements_inserted += 1
            by_code[rdef["code"]] = req

        for rdef in definition["requirements"]:
            parent_code = rdef.get("parent")
            if parent_code:
                by_code[rdef["code"]].parent_id = by_code[parent_code].id
        self.session.flush()

        # --- eligibility ---
        # Each node's rule is STORED, then eligibility is reconciled to
        # exactly what the rules imply over the current course table - rows
        # a changed rule no longer implies are removed, not left behind
        # (app.services.eligibility). A node whose rule was removed keeps an
        # empty rule rather than NULL, so its old rows are removed too; NULL
        # means "eligibility comes from elsewhere" (SAS Core certifications).
        for rdef in definition["requirements"]:
            req = by_code[rdef["code"]]
            rule = rule_from_definition(rdef)
            if rule is None and req.eligibility_rule is not None:
                rule = {}
            req.eligibility_rule = rule
        self.session.flush()
        report = reconcile(self.session, [r.id for r in by_code.values()])
        stats.eligibility_inserted += report.inserted
        stats.eligibility_removed += report.deleted
        stats.unresolved_courses.extend(report.unresolved)

        # --- program-level rules ---
        # Inserted or refreshed in place (Phase 6.4: before, an existing rule
        # was skipped, so a re-curated threshold or reason never reached the
        # database). Rules a definition no longer states are removed.
        stated = set()
        for rdef in definition.get("program_rules", []):
            stated.add(rdef["code"])
            evaluable = rdef.get("is_evaluable", True)
            fields = dict(
                name=rdef["name"],
                rule_type=rdef["rule_type"],
                grade=rdef.get("grade"),
                max_count=rdef.get("max_count"),
                min_count=rdef.get("min_count"),
                subject_code=rdef.get("subject_code"),
                offering_unit_code=rdef.get("offering_unit_code"),
                excluded_course_strings=rdef.get("excluded_course_strings"),
                min_gpa=_dec(rdef.get("min_gpa")),
                gpa_scope=rdef.get("gpa_scope"),
                is_evaluable=evaluable,
                not_evaluable_reason=rdef.get("not_evaluable_reason"),
                source_prose=rdef.get("source_prose"),
                curation_status=curation,
            )
            existing = self.session.scalar(
                select(ProgramRule).where(
                    ProgramRule.program_version_id == version.id,
                    ProgramRule.code == rdef["code"],
                )
            )
            if existing is not None:
                for attr, value in fields.items():
                    setattr(existing, attr, value)
                continue
            self.session.add(ProgramRule(program_version_id=version.id, code=rdef["code"],
                                         source_id=source.id, **fields))
            stats.rules_inserted += 1
            if not evaluable:
                stats.rules_not_evaluable += 1
        for stale in self.session.scalars(select(ProgramRule).where(
                ProgramRule.program_version_id == version.id)):
            if stale.code not in stated:
                self.session.delete(stale)

        self.session.flush()
        self._link_components(definition, version)

        if stats.unresolved_courses:
            logger.warning(
                "%d requirement course(s) could not be resolved: %s",
                len(stats.unresolved_courses),
                stats.unresolved_courses[:10],
            )
        for rule in definition.get("rules_not_yet_modeled", []):
            stats.warnings.append(f"not modeled: {rule['rule']}")

        return stats


    def _link_components(self, definition: dict, version) -> None:
        """Prompt 08 B1: compose the components the definition names - and
        only those - with this program version (`program_version_component`).
        A named component that is not loaded is an error: composing less than
        the definition states would under-report requirements."""
        from app.models import ProgramVersionComponent, SchoolRequirementSetVersion

        found = {}
        for order, code in enumerate(definition.get("components", [])):
            comp = self.session.scalar(select(SchoolRequirementSetVersion).where(
                SchoolRequirementSetVersion.code == code,
                SchoolRequirementSetVersion.catalog_year == version.catalog_year))
            if comp is None:
                raise ValueError(f"component {code!r} ({version.catalog_year}) is not loaded")
            found[comp.id] = order
        existing = {link.component_id: link for link in self.session.scalars(
            select(ProgramVersionComponent).where(
                ProgramVersionComponent.program_version_id == version.id))}
        for comp_id, link in existing.items():
            if comp_id not in found:
                self.session.delete(link)
        for comp_id, order in found.items():
            if comp_id in existing:
                existing[comp_id].sort_order = order
            else:
                self.session.add(ProgramVersionComponent(
                    program_version_id=version.id, component_id=comp_id, sort_order=order))
        self.session.flush()


#: Top-level keys that carry requirement meaning. `_README`, `source`,
#: `curation` and `rules_not_yet_modeled` are documentation and provenance:
#: editing them must not invalidate a review.
SEMANTIC_KEYS = ("school", "program", "program_version", "requirements", "program_rules",
                 "components")


def definition_sha256(definition: dict) -> str:
    """Content identity of a curated definition: what a reviewer approves."""
    # A key a definition does not state is left out, so adding an optional
    # key (Prompt 08 B1 "components") leaves every existing hash unchanged.
    semantic = {k: definition.get(k) for k in SEMANTIC_KEYS
                if k in definition or k in SEMANTIC_KEYS[:5]}
    canonical = json.dumps(semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _grade_semantics(rdef: dict) -> dict:
    """Phase 6.4 requirement fields, validated against the Rutgers scale."""
    quota = rdef.get("grade_quota") or {}
    if quota and (quota.get("at_most", "").upper() not in LETTER_POINTS
                  or int(quota.get("max_count", -1)) < 0):
        raise ValueError(f"{rdef['code']}: grade_quota needs max_count >= 0 and a letter at_most")
    sequences = rdef.get("category_sequences")
    if sequences:
        for category, seqs in sequences.items():
            if not all(isinstance(seq, list) and len(seq) >= 2 for seq in seqs):
                raise ValueError(f"{rdef['code']}: sequence for {category} needs 2+ courses")
    return {
        "min_grade": validate_minimum(rdef["min_grade"]) if rdef.get("min_grade") else None,
        "grade_quota_max_count": int(quota["max_count"]) if quota else None,
        "grade_quota_at_most": quota["at_most"].upper() if quota else None,
        "category_sequences": ({k: [list(seq) for seq in v] for k, v in sorted(sequences.items())}
                               if sequences else None),
    }


def _b1_fields(rdef: dict) -> dict:
    """Prompt 08 B1 node columns (the component loader sets the same ones)."""
    caps = rdef.get("group_caps")
    for cap in caps or ():
        if not cap.get("courses") or int(cap.get("max", -1)) < 0:
            raise ValueError(f"{rdef['code']}: group_caps entries need courses and max >= 0")
    return {
        "available_after_stage": rdef.get("available_after_stage"),
        "min_course_credits": _dec(rdef.get("min_course_credits")),
        "certification_codes": sorted(set(rdef["certification_codes"]))
        if rdef.get("certification_codes") else None,
        "group_caps": caps or None,
        "unresolved_conflicts": sorted(set(rdef["unresolved_conflicts"]))
        if rdef.get("unresolved_conflicts") else None,
    }


def _prose(rdef: dict) -> str | None:
    """`source_prose`, or a multi-source node's quotes joined with " ... "."""
    if rdef.get("source_prose"):
        return rdef["source_prose"]
    quotes = [q["quote"] for q in rdef.get("quotes", []) if q.get("quote")]
    return " ... ".join(quotes) or None


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))
