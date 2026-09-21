"""Loader: SAS Core goals and eligibility -> the EXISTING requirement tables.

No Core-specific tables, and no Core-specific program. Core joins the
STUDENT'S existing program version:

    Program (e.g. CS 198 BA)
      -> ProgramVersion(sharing_policy='share_across_systems')
        -> Requirement(requirement_system='major')   the major tree
        -> Requirement(requirement_system='core')    the core tree, added here
          -> RequirementCourseOption                 course -> goal eligibility

Two roots in one tree, distinguished only by `requirement_system`.

That reuse is the point. A parallel `CoreRequirement` hierarchy would need its
own audit engine, its own allocator, and its own sharing rules - and the one
thing Core most needs (sharing with the major) already exists. It also would
not work: the audit engine evaluates ONE ProgramVersion, so core requirements
stored elsewhere would never appear in a student's audit.

Rules it enforces, consistent with every other loader:

  * **Never create a Course.** A certification naming a course outside the
    ingested SOC terms is reported, never manufactured.
  * **Never mix catalog years.** Every row carries the run's catalog year.
  * **Idempotent.** Re-loading updates in place.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from decimal import Decimal

from app.models import (
    Course,
    CurationStatus,
    DataSource,
    Program,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    RequirementSystem,
    School,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from coursepilot_ingestion.core_schemas import (
    CoreIngestionStats,
    NormalizedCoreEligibility,
    NormalizedCoreGoal,
)

logger = logging.getLogger(__name__)


class CoreLoader:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ #
    # provenance
    # ------------------------------------------------------------------ #

    def get_or_create_source(
        self, source_def: dict, raw: bytes, catalog_year: str
    ) -> DataSource:
        content_hash = hashlib.sha256(raw).hexdigest()
        existing = self.session.scalar(
            select(DataSource).where(
                DataSource.content_hash == content_hash,
                DataSource.academic_year == catalog_year,
            )
        )
        if existing is not None:
            return existing

        source = DataSource(
            kind=source_def["kind"],
            url=source_def["url"],
            title=f"SAS Core Curriculum {catalog_year}",
            retrieved_at=datetime.fromisoformat(source_def["retrieved_at"]).replace(tzinfo=UTC),
            content_hash=content_hash,
            academic_year=catalog_year,
        )
        self.session.add(source)
        self.session.flush()
        return source

    # ------------------------------------------------------------------ #
    # structure
    # ------------------------------------------------------------------ #

    def _resolve_target_version(
        self, definition, catalog_year: str
    ) -> ProgramVersion:
        """Find the EXISTING program version Core attaches to.

        Core requirements do not get their own program. They join the
        student's program version tagged `requirement_system='core'`, because:

          * the audit engine evaluates ONE ProgramVersion, so core
            requirements in a separate version would be invisible to a
            student's audit; and
          * sharing only works when major and core slots are in the same tree.

        This raises rather than creating a program. A missing target means the
        major has not been ingested, and inventing one would produce a Core
        attached to a program nobody is enrolled in.
        """
        target = definition.target_program
        version = self.session.scalar(
            select(ProgramVersion)
            .join(Program, Program.id == ProgramVersion.program_id)
            .join(School, School.id == Program.school_id)
            .where(
                School.code == target["school_code"],
                Program.code == target["program_code"],
                Program.degree_type == target["degree_type"],
                ProgramVersion.catalog_year == catalog_year,
            )
        )
        if version is None:
            raise ValueError(
                f"target program version not found: {target['school_code']}/"
                f"{target['program_code']}/{target['degree_type']} {catalog_year}. "
                "Core attaches to an existing program version - ingest the program's "
                "requirements first."
            )

        # Core is the reason this program must share. Applied here rather than
        # left to the major's definition, because the sharing permission comes
        # from the SAS core source, not from the major's catalog page.
        policy = definition.program_version.get("sharing_policy")
        if policy and version.sharing_policy != policy:
            logger.info(
                "setting sharing_policy=%s on %s %s (was %s) - required by SAS core",
                policy,
                target["program_code"],
                catalog_year,
                version.sharing_policy,
            )
            version.sharing_policy = policy
            self.session.flush()

        return version

    def _load_requirements(
        self,
        definition,
        version: ProgramVersion,
        source: DataSource,
        stats: CoreIngestionStats,
    ) -> tuple[dict[str, Requirement], dict[str, list[str]]]:
        """Create the requirement tree and map goal code -> requirement codes.

        A goal may feed several requirements (AHp feeds CORE_AH), and a
        requirement may accept several goals (CORE_WC accepts WCr and WCd), so
        the mapping is many-to-many in both directions.
        """
        curation = definition.source.get("curation_status", CurationStatus.UNVERIFIED.value)
        system = RequirementSystem.CORE.value

        by_code: dict[str, Requirement] = {}
        goal_to_requirements: dict[str, list[str]] = {}

        for rdef in definition.requirements:
            existing = self.session.scalar(
                select(Requirement).where(
                    Requirement.program_version_id == version.id,
                    Requirement.code == rdef["code"],
                )
            )
            if existing is not None:
                existing.name = rdef["name"]
                existing.requirement_type = rdef["requirement_type"]
                existing.requirement_system = system
                existing.sort_order = rdef.get("sort_order", 0)
                existing.min_distinct_categories = rdef.get("min_distinct_categories")
                stats.requirements_updated += 1
                by_code[rdef["code"]] = existing
            else:
                req = Requirement(
                    program_version_id=version.id,
                    code=rdef["code"],
                    name=rdef["name"],
                    requirement_type=rdef["requirement_type"],
                    requirement_system=system,
                    sort_order=rdef.get("sort_order", 0),
                    min_count=rdef.get("min_count"),
                    min_distinct_categories=rdef.get("min_distinct_categories"),
                    min_credits=(
                        Decimal(str(rdef["min_credits"]))
                        if rdef.get("min_credits") is not None
                        else None
                    ),
                    notes=rdef.get("notes"),
                    source_prose=rdef.get("source_prose"),
                    curation_status=curation,
                    source_id=source.id,
                )
                self.session.add(req)
                self.session.flush()
                stats.requirements_inserted += 1
                by_code[rdef["code"]] = req

            # A node may name one goal or several.
            goals = rdef.get("goals") or ([rdef["goal"]] if rdef.get("goal") else [])
            for goal_code in goals:
                goal_to_requirements.setdefault(goal_code.casefold(), []).append(rdef["code"])

        for rdef in definition.requirements:
            parent_code = rdef.get("parent")
            if parent_code:
                by_code[rdef["code"]].parent_id = by_code[parent_code].id
        self.session.flush()

        return by_code, goal_to_requirements

    # ------------------------------------------------------------------ #
    # eligibility
    # ------------------------------------------------------------------ #

    def _resolve_course(self, course_string: str) -> Course | None:
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

    def _load_eligibility(
        self,
        eligibility: list[NormalizedCoreEligibility],
        by_code: dict[str, Requirement],
        goal_to_requirements: dict[str, list[str]],
        canonical_goal: dict[str, str],
        source: DataSource,
        stats: CoreIngestionStats,
    ) -> None:
        resolved_courses: set[str] = set()
        unresolved: set[str] = set()

        for item in eligibility:
            requirement_codes = goal_to_requirements.get(item.match_key)
            if not requirement_codes:
                # A goal we defined but wired to no requirement node. Counted
                # rather than silently skipped.
                stats.unmapped_goal_codes[item.goal_code] = (
                    stats.unmapped_goal_codes.get(item.goal_code, 0) + 1
                )
                continue

            course = self._resolve_course(item.course_string)
            if course is None:
                # NEVER manufacture a Course. The certification is real but we
                # have no SOC record for the course in the ingested terms.
                unresolved.add(item.course_string)
                continue
            resolved_courses.add(item.course_string)

            for req_code in requirement_codes:
                requirement = by_code[req_code]
                # The CURATED goal spelling, not SOC's. The SAS page is
                # authoritative for goal identity (it writes WCR/WCD where SOC
                # writes WCr/WCd), and storing the certifying goal is what
                # makes "meet at least two of these goals" evaluable.
                category = canonical_goal.get(item.match_key, item.goal_code)
                exists = self.session.scalar(
                    select(RequirementCourseOption).where(
                        RequirementCourseOption.requirement_id == requirement.id,
                        RequirementCourseOption.course_id == course.id,
                        RequirementCourseOption.category == category,
                    )
                )
                if exists is not None:
                    continue
                self.session.add(
                    RequirementCourseOption(
                        requirement_id=requirement.id,
                        course_id=course.id,
                        category=category,
                        source_id=source.id,
                    )
                )
                stats.eligibility_inserted += 1

        stats.courses_represented = len(resolved_courses)
        stats.unresolved_courses = sorted(unresolved)
        self.session.flush()

        if unresolved:
            logger.info(
                "%d certified course(s) are not in the ingested SOC terms "
                "(reported, not created): %s",
                len(unresolved),
                sorted(unresolved)[:10],
            )

    # ------------------------------------------------------------------ #
    # entry point
    # ------------------------------------------------------------------ #

    def load(
        self,
        definition,
        goals: list[NormalizedCoreGoal],
        eligibility: list[NormalizedCoreEligibility],
        raw_definition: bytes,
        catalog_year: str,
        stats: CoreIngestionStats,
    ) -> CoreIngestionStats:
        source = self.get_or_create_source(definition.source, raw_definition, catalog_year)
        version = self._resolve_target_version(definition, catalog_year)
        by_code, goal_to_requirements = self._load_requirements(
            definition, version, source, stats
        )
        stats.goals_defined = len(goals)
        canonical_goal = {g.match_key: g.code for g in goals}
        self._load_eligibility(
            eligibility, by_code, goal_to_requirements, canonical_goal, source, stats
        )

        certified = {e.match_key for e in eligibility}
        stats.goals_without_eligibility = sorted(
            g.code for g in goals if g.match_key not in certified
        )
        return stats
