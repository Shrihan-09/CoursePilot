"""Loader for curated program requirements.

Input is a curated JSON definition (see
`ingestion/tests/fixtures/cs_ba_requirements_26_27.json`), NOT a scraped
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
    RequirementCourseOption,
    School,
)
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
    rules_inserted: int = 0
    rules_not_evaluable: int = 0
    unresolved_courses: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"schools+{self.schools_inserted} programs+{self.programs_inserted} "
            f"versions+{self.versions_inserted} "
            f"requirements(+{self.requirements_inserted}/~{self.requirements_updated}) "
            f"eligibility+{self.eligibility_inserted} "
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
        """Resolve an `eligible_course_query` (e.g. all CS courses at 300+)."""
        stmt = select(Course).where(Course.supplement_code == "")
        if "subject_code" in spec:
            stmt = stmt.where(Course.subject_code == spec["subject_code"])
        courses = list(self.session.scalars(stmt).all())
        if "min_course_number" in spec:
            floor = int(spec["min_course_number"])
            courses = [
                c for c in courses if c.course_number.isdigit() and int(c.course_number) >= floor
            ]
        return sorted(courses, key=lambda c: c.course_string)

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
        program = self.session.scalar(
            select(Program).where(
                Program.school_id == school.id,
                Program.code == pdef["code"],
                Program.degree_type == pdef["degree_type"],
            )
        )
        if program is None:
            program = Program(
                school_id=school.id,
                code=pdef["code"],
                name=pdef["name"],
                degree_type=pdef["degree_type"],
                source_id=source.id,
            )
            self.session.add(program)
            self.session.flush()
            stats.programs_inserted += 1

        # --- version ---
        vdef = definition["program_version"]
        version = self.session.scalar(
            select(ProgramVersion).where(
                ProgramVersion.program_id == program.id,
                ProgramVersion.catalog_year == vdef["catalog_year"],
            )
        )
        if version is None:
            version = ProgramVersion(
                program_id=program.id,
                catalog_year=vdef["catalog_year"],
                total_credits_min=_dec(vdef.get("total_credits_min")),
                total_credits_max=_dec(vdef.get("total_credits_max")),
                # Defaults to EXCLUSIVE: a definition that says nothing about
                # sharing must not silently grant it.
                sharing_policy=vdef.get("sharing_policy", "exclusive"),
                source_prose=vdef.get("source_prose"),
                source_url=definition["source"]["url"],
                curation_status=curation,
                source_id=source.id,
            )
            self.session.add(version)
            self.session.flush()
            stats.versions_inserted += 1

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
                existing.name = rdef["name"]
                existing.requirement_type = rdef["requirement_type"]
                existing.requirement_system = rdef.get("requirement_system", "major")
                existing.sort_order = rdef.get("sort_order", 0)
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
                source_prose=rdef.get("source_prose"),
                curation_status=curation,
                source_id=source.id,
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
        for rdef in definition["requirements"]:
            req = by_code[rdef["code"]]
            # (course, category). `category` is the source's own certification
            # identifier - a SAS Core goal code, for instance. It stays empty
            # for requirements whose source certifies no sub-categories, which
            # is every major requirement.
            options: list[tuple[Course, str]] = []

            for cs in rdef.get("courses", []):
                course = self._resolve_course(cs)
                if course is None:
                    # Reported, never fabricated.
                    stats.unresolved_courses.append(f"{rdef['code']}:{cs}")
                    continue
                options.append((course, ""))

            # {"AHp": ["01:082:105", ...]} - one row per certifying category,
            # so a course certified for two categories produces two rows.
            for category, course_strings in rdef.get("course_categories", {}).items():
                for cs in course_strings:
                    course = self._resolve_course(cs)
                    if course is None:
                        stats.unresolved_courses.append(f"{rdef['code']}:{cs}")
                        continue
                    options.append((course, category))

            if "eligible_course_query" in rdef:
                options.extend(
                    (c, "") for c in self._query_courses(rdef["eligible_course_query"])
                )

            for course, category in options:
                exists = self.session.scalar(
                    select(RequirementCourseOption).where(
                        RequirementCourseOption.requirement_id == req.id,
                        RequirementCourseOption.course_id == course.id,
                        RequirementCourseOption.category == category,
                    )
                )
                if exists is not None:
                    continue
                self.session.add(
                    RequirementCourseOption(
                        requirement_id=req.id,
                        course_id=course.id,
                        category=category,
                        source_id=source.id,
                    )
                )
                stats.eligibility_inserted += 1

        # --- program-level rules ---
        for rdef in definition.get("program_rules", []):
            existing = self.session.scalar(
                select(ProgramRule).where(
                    ProgramRule.program_version_id == version.id,
                    ProgramRule.code == rdef["code"],
                )
            )
            if existing is not None:
                continue

            evaluable = rdef.get("is_evaluable", True)
            self.session.add(
                ProgramRule(
                    program_version_id=version.id,
                    code=rdef["code"],
                    name=rdef["name"],
                    rule_type=rdef["rule_type"],
                    grade=rdef.get("grade"),
                    max_count=rdef.get("max_count"),
                    min_count=rdef.get("min_count"),
                    subject_code=rdef.get("subject_code"),
                    offering_unit_code=rdef.get("offering_unit_code"),
                    excluded_course_strings=rdef.get("excluded_course_strings"),
                    is_evaluable=evaluable,
                    not_evaluable_reason=rdef.get("not_evaluable_reason"),
                    source_prose=rdef.get("source_prose"),
                    curation_status=curation,
                    source_id=source.id,
                )
            )
            stats.rules_inserted += 1
            if not evaluable:
                stats.rules_not_evaluable += 1

        self.session.flush()

        if stats.unresolved_courses:
            logger.warning(
                "%d requirement course(s) could not be resolved: %s",
                len(stats.unresolved_courses),
                stats.unresolved_courses[:10],
            )
        for rule in definition.get("rules_not_yet_modeled", []):
            stats.warnings.append(f"not modeled: {rule['rule']}")

        return stats


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))
