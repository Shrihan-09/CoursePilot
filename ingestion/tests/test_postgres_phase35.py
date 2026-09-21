"""PostgreSQL integration tests for Phase 3.5.

Reuses the database guards from `test_postgres_integration`: these TRUNCATE,
so they run only against a database whose name ends in `_test`.

Verifies what SQLite cannot prove for the new schema:
  * catalog_course_entry -> course FK, and that a NULL link is permitted
  * catalog-year uniqueness on (course_string, catalog_year)
  * program_rule CHECK constraints, including "not evaluable needs a reason"
  * CASCADE from program_version down to rules
  * Numeric precision on catalog credit ranges
  * idempotency of both new loaders
"""

from __future__ import annotations

import pathlib
import shutil
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.models import (
    CatalogCourseEntry,
    Course,
    ProgramRule,
    ProgramVersion,
    Student,
    StudentCourse,
)
from coursepilot_ingestion.loaders.postgres import CourseLoader
from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.normalizers.soc import SocNormalizer
from coursepilot_ingestion.parsers.soc import SocParser
from coursepilot_ingestion.pipelines.catalog import CatalogIngestionPipeline
from coursepilot_ingestion.schemas import IngestionStats
from coursepilot_ingestion.sources.catalog import CatalogQuery
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from tests.test_postgres_integration import DB_URL, pg_session, requires_postgres  # noqa: F401

pytestmark = pytest.mark.db

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CS_REQUIREMENTS = FIXTURES / "cs_ba_requirements_26_27.json"
TERM = "20269"
Y26 = "2026-2027"
Y25 = "2025-2026"


@pytest.fixture
def catalog_cache(tmp_path: pathlib.Path) -> pathlib.Path:
    for year, name in ((Y26, "cs_26-27.html"), (Y25, "cs_25-26.html")):
        src = REPO_ROOT / "data" / "raw" / "catalog" / name
        if not src.exists():
            pytest.skip(f"catalog archive missing: {src}")
        shutil.copyfile(src, tmp_path / CatalogQuery(catalog_year=year).archive_name)
    return tmp_path


def _seed(pg_session, cs_payload_bytes: bytes):
    pg_session.execute(
        text(
            "TRUNCATE student_course, student, requirement_course_option, requirement, "
            "program_rule, program_version, program, school, catalog_course_entry "
            "RESTART IDENTITY CASCADE"
        )
    )
    pg_session.commit()

    courses = [
        SocNormalizer(term_code=TERM).normalize(r)
        for r in SocParser().parse(cs_payload_bytes).courses
    ]
    loader = CourseLoader(pg_session)
    source = loader.get_or_create_source(
        kind="rutgers_official_api",
        url="https://example.invalid/cs-courses",
        content_hash="pg35-cs-courses",
        retrieved_at=datetime.now(UTC),
        term_code=TERM,
        academic_year="2026",
        archive_path=None,
        record_count=len(courses),
    )
    loader.load(courses, source, IngestionStats())
    pg_session.flush()
    stats = RequirementLoader(pg_session).load_file(CS_REQUIREMENTS)
    pg_session.commit()
    return stats


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------


@requires_postgres
def test_phase35_tables_exist() -> None:
    from sqlalchemy import create_engine, inspect

    engine = create_engine(DB_URL, future=True)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()

    assert {"program_rule", "catalog_course_entry"} <= tables


# --------------------------------------------------------------------------
# catalog entries
# --------------------------------------------------------------------------


@requires_postgres
def test_catalog_pipeline_on_postgres(
    pg_session, cs_payload_bytes: bytes, catalog_cache: pathlib.Path
) -> None:
    _seed(pg_session, cs_payload_bytes)
    stats = CatalogIngestionPipeline(pg_session, catalog_cache).run(
        CatalogQuery(catalog_year=Y26)
    )

    assert stats.entries_inserted == 44
    assert stats.descriptions_populated == 44
    assert pg_session.scalar(select(func.count()).select_from(CatalogCourseEntry)) == 44


@requires_postgres
def test_unlinked_catalog_entry_is_allowed(
    pg_session, cs_payload_bytes: bytes, catalog_cache: pathlib.Path
) -> None:
    """course_id must be nullable: the catalog is a superset of SOC."""
    _seed(pg_session, cs_payload_bytes)
    CatalogIngestionPipeline(pg_session, catalog_cache).run(CatalogQuery(catalog_year=Y26))

    unlinked = pg_session.scalars(
        select(CatalogCourseEntry).where(CatalogCourseEntry.course_id.is_(None))
    ).all()
    assert unlinked
    assert all(e.course_string and e.description for e in unlinked)


@requires_postgres
def test_catalog_entry_rejects_a_bogus_course_fk(
    pg_session, cs_payload_bytes: bytes
) -> None:
    """Nullable does not mean unchecked - a non-NULL link must be real."""
    _seed(pg_session, cs_payload_bytes)
    source_id = pg_session.scalar(select(ProgramVersion.source_id))

    pg_session.add(
        CatalogCourseEntry(
            course_string="01:198:999",
            course_id=uuid.uuid4(),  # no such course
            catalog_year=Y26,
            title="Bogus",
            source_id=source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_catalog_year_uniqueness_enforced(pg_session, cs_payload_bytes: bytes) -> None:
    _seed(pg_session, cs_payload_bytes)
    source_id = pg_session.scalar(select(ProgramVersion.source_id))

    for _ in range(2):
        pg_session.add(
            CatalogCourseEntry(
                course_string="01:198:111",
                catalog_year=Y26,
                title="Dup",
                source_id=source_id,
            )
        )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_same_course_in_two_catalog_years_is_allowed(
    pg_session, cs_payload_bytes: bytes, catalog_cache: pathlib.Path
) -> None:
    """Identical content does not mean identical version identity."""
    _seed(pg_session, cs_payload_bytes)
    pipeline = CatalogIngestionPipeline(pg_session, catalog_cache)
    pipeline.run(CatalogQuery(catalog_year=Y26))
    pipeline.run(CatalogQuery(catalog_year=Y25))

    rows = pg_session.scalars(
        select(CatalogCourseEntry).where(CatalogCourseEntry.course_string == "01:198:111")
    ).all()
    assert {r.catalog_year for r in rows} == {Y26, Y25}
    assert pg_session.scalar(select(func.count()).select_from(CatalogCourseEntry)) == 88


@requires_postgres
def test_catalog_credit_range_round_trips(
    pg_session, cs_payload_bytes: bytes, catalog_cache: pathlib.Path
) -> None:
    _seed(pg_session, cs_payload_bytes)
    CatalogIngestionPipeline(pg_session, catalog_cache).run(CatalogQuery(catalog_year=Y26))

    entry = pg_session.scalar(
        select(CatalogCourseEntry).where(CatalogCourseEntry.course_string == "01:198:442")
    )
    assert isinstance(entry.credits_min, Decimal)
    assert (entry.credits_min, entry.credits_max) == (Decimal("3.0"), Decimal("4.0"))


@requires_postgres
def test_catalog_pipeline_idempotent_on_postgres(
    pg_session, cs_payload_bytes: bytes, catalog_cache: pathlib.Path
) -> None:
    _seed(pg_session, cs_payload_bytes)
    pipeline = CatalogIngestionPipeline(pg_session, catalog_cache)
    pipeline.run(CatalogQuery(catalog_year=Y26))
    before = pg_session.scalar(select(func.count()).select_from(CatalogCourseEntry))

    second = pipeline.run(CatalogQuery(catalog_year=Y26))

    assert second.entries_inserted == 0
    assert pg_session.scalar(select(func.count()).select_from(CatalogCourseEntry)) == before


@requires_postgres
def test_soc_credits_untouched_on_postgres(
    pg_session, cs_payload_bytes: bytes, catalog_cache: pathlib.Path
) -> None:
    _seed(pg_session, cs_payload_bytes)
    course = pg_session.scalar(
        select(Course).where(Course.course_string == "01:198:111", Course.supplement_code == "")
    )
    before = course.credits

    CatalogIngestionPipeline(pg_session, catalog_cache).run(CatalogQuery(catalog_year=Y26))
    pg_session.refresh(course)

    assert course.credits == before


# --------------------------------------------------------------------------
# program rules
# --------------------------------------------------------------------------


@requires_postgres
def test_program_rules_load_on_postgres(pg_session, cs_payload_bytes: bytes) -> None:
    stats = _seed(pg_session, cs_payload_bytes)

    assert stats.rules_inserted == 3
    assert stats.rules_not_evaluable == 1
    assert pg_session.scalar(select(func.count()).select_from(ProgramRule)) == 3


@requires_postgres
def test_unknown_rule_type_rejected(pg_session, cs_payload_bytes: bytes) -> None:
    _seed(pg_session, cs_payload_bytes)
    existing = pg_session.scalar(select(ProgramRule))

    pg_session.add(
        ProgramRule(
            program_version_id=existing.program_version_id,
            code="BOGUS",
            name="bogus",
            rule_type="invent_a_rule",
            source_id=existing.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_not_evaluable_rule_requires_a_reason(pg_session, cs_payload_bytes: bytes) -> None:
    """A rule we cannot check must say why, or it is a silent omission."""
    _seed(pg_session, cs_payload_bytes)
    existing = pg_session.scalar(select(ProgramRule))

    pg_session.add(
        ProgramRule(
            program_version_id=existing.program_version_id,
            code="NO_REASON",
            name="unevaluable without explanation",
            rule_type="residency",
            is_evaluable=False,
            not_evaluable_reason=None,
            source_id=existing.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_duplicate_rule_code_rejected(pg_session, cs_payload_bytes: bytes) -> None:
    _seed(pg_session, cs_payload_bytes)
    existing = pg_session.scalar(select(ProgramRule).where(ProgramRule.code == "CS_MAX_D"))

    pg_session.add(
        ProgramRule(
            program_version_id=existing.program_version_id,
            code="CS_MAX_D",
            name="duplicate",
            rule_type="max_grade_count",
            source_id=existing.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_deleting_a_version_cascades_to_rules(pg_session, cs_payload_bytes: bytes) -> None:
    _seed(pg_session, cs_payload_bytes)
    version = pg_session.scalar(select(ProgramVersion))
    assert pg_session.scalar(select(func.count()).select_from(ProgramRule)) == 3

    pg_session.execute(
        text("DELETE FROM program_version WHERE id = :i"), {"i": version.id}
    )
    pg_session.commit()
    pg_session.expunge_all()

    assert pg_session.scalar(select(func.count()).select_from(ProgramRule)) == 0


@requires_postgres
def test_requirement_load_still_idempotent_with_rules(
    pg_session, cs_payload_bytes: bytes
) -> None:
    _seed(pg_session, cs_payload_bytes)
    second = RequirementLoader(pg_session).load_file(CS_REQUIREMENTS)
    pg_session.commit()

    assert second.rules_inserted == 0
    assert pg_session.scalar(select(func.count()).select_from(ProgramRule)) == 3


# --------------------------------------------------------------------------
# audit with rules, end to end on PostgreSQL
# --------------------------------------------------------------------------


@requires_postgres
def test_audit_with_rules_on_postgres(pg_session, cs_payload_bytes: bytes) -> None:
    from app.domain.audit import AuditStatus, RequirementStatus
    from app.services.audit import DegreeAuditEngine

    _seed(pg_session, cs_payload_bytes)
    version = pg_session.scalar(select(ProgramVersion))
    student = Student(
        external_ref="pg35-audit",
        catalog_year=version.catalog_year,
        program_version_id=version.id,
    )
    pg_session.add(student)
    pg_session.flush()

    # An excluded course plus a counted one.
    for code, grade in (("01:198:405", "A"), ("01:198:111", "A")):
        course = pg_session.scalar(
            select(Course).where(Course.course_string == code, Course.supplement_code == "")
        )
        pg_session.add(
            StudentCourse(
                student_id=student.id,
                course_id=course.id,
                term_code=TERM,
                status="completed",
                grade=grade,
                credits_earned=course.credits,
            )
        )
    pg_session.commit()

    result = DegreeAuditEngine(pg_session).audit(student)

    assert result.credits_completed == Decimal(7)
    assert result.credits_applicable_to_degree == Decimal(4)
    assert result.credits_excluded == Decimal(3)
    assert len(result.rules) == 3
    residency = next(r for r in result.rules if r.rule_code == "CS_RESIDENCY")
    assert residency.status is RequirementStatus.NOT_EVALUABLE
    assert result.status is not AuditStatus.COMPLETE


# ==========================================================================
# Phase 3.75 - requirement sharing schema
# ==========================================================================


@requires_postgres
def test_sharing_columns_backfilled_safely(pg_session, cs_payload_bytes: bytes) -> None:
    """The migration added two NOT NULL columns to populated tables.

    Both defaults are the SAFE values, so applying it cannot change any
    existing audit: every pre-existing requirement becomes `major` and every
    pre-existing program stays `exclusive`.
    """
    from app.models import Requirement

    _seed(pg_session, cs_payload_bytes)

    version = pg_session.scalar(select(ProgramVersion))
    assert version.sharing_policy == "exclusive"

    systems = {
        r.requirement_system for r in pg_session.scalars(select(Requirement)).all()
    }
    assert systems == {"major"}


@requires_postgres
def test_unknown_sharing_policy_rejected(pg_session, cs_payload_bytes: bytes) -> None:
    """Closed set: an unknown policy would make the allocator fall back to a
    default silently, so PostgreSQL refuses it."""
    _seed(pg_session, cs_payload_bytes)
    version = pg_session.scalar(select(ProgramVersion))
    version.sharing_policy = "share_everything_always"

    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_unknown_requirement_system_is_allowed(pg_session, cs_payload_bytes: bytes) -> None:
    """Open set, unlike sharing_policy.

    Rutgers has more requirement systems than we have modeled (school
    requirements, general education). Adding one must not need a migration, so
    requirement_system is deliberately unconstrained.
    """
    from app.models import Requirement

    _seed(pg_session, cs_payload_bytes)
    existing = pg_session.scalar(select(Requirement))

    pg_session.add(
        Requirement(
            program_version_id=existing.program_version_id,
            code="SCHOOL_REQ",
            name="A system we have not modeled yet",
            requirement_type="all_of",
            requirement_system="school_requirements",
            source_id=existing.source_id,
        )
    )
    pg_session.commit()  # must NOT raise

    saved = pg_session.scalar(
        select(Requirement).where(Requirement.code == "SCHOOL_REQ")
    )
    assert saved.requirement_system == "school_requirements"


@requires_postgres
def test_shared_allocation_does_not_double_count_credits_on_postgres(
    pg_session, cs_payload_bytes: bytes
) -> None:
    """Case A, verified on PostgreSQL with real Numeric credits."""
    import json as _json

    from app.domain.audit import RequirementStatus
    from app.services.audit import DegreeAuditEngine
    from coursepilot_ingestion.loaders.requirements import RequirementLoader

    _seed(pg_session, cs_payload_bytes)

    definition = {
        "source": {
            "url": "synthetic://coursepilot/test/pg-sharing",
            "catalog_year": "2031-2032",
            "retrieved_at": "2026-09-14",
            "kind": "manual_curation",
            "curation_status": "synthetic",
        },
        "school": {"code": "SAS", "name": "School of Arts and Sciences"},
        "program": {"code": "998", "name": "PG Sharing Program", "degree_type": "BA"},
        "program_version": {
            "catalog_year": "2031-2032",
            "total_credits_min": 12,
            "sharing_policy": "share_across_systems",
            "source_prose": "SYNTHETIC",
        },
        "requirements": [
            {
                "code": "PG_MAJOR",
                "name": "Major slot",
                "requirement_type": "course",
                "parent": None,
                "requirement_system": "major",
                "courses": ["01:198:111"],
                "source_prose": "SYNTHETIC",
            },
            {
                "code": "PG_CORE",
                "name": "Core slot",
                "requirement_type": "course",
                "parent": None,
                "requirement_system": "core",
                "courses": ["01:198:111"],
                "source_prose": "SYNTHETIC",
            },
        ],
    }
    RequirementLoader(pg_session).load(definition, _json.dumps(definition).encode())
    pg_session.commit()

    version = pg_session.scalar(
        select(ProgramVersion).where(ProgramVersion.catalog_year == "2031-2032")
    )
    student = Student(
        external_ref="pg-sharing",
        catalog_year="2031-2032",
        program_version_id=version.id,
    )
    pg_session.add(student)
    pg_session.flush()

    course = pg_session.scalar(
        select(Course).where(Course.course_string == "01:198:111", Course.supplement_code == "")
    )
    pg_session.add(
        StudentCourse(
            student_id=student.id,
            course_id=course.id,
            term_code=TERM,
            status="completed",
            grade="A",
            credits_earned=course.credits,
        )
    )
    pg_session.commit()

    result = DegreeAuditEngine(pg_session).audit(student)

    # Both requirements satisfied by ONE course...
    satisfied = [
        r.requirement_code
        for r in result.requirements
        if r.status is RequirementStatus.SATISFIED
    ]
    assert sorted(satisfied) == ["PG_CORE", "PG_MAJOR"]
    assert len(result.allocation) == 2

    # ...and the credits are counted once, as a real Decimal.
    assert isinstance(result.credits_applicable_to_degree, Decimal)
    assert result.credits_applicable_to_degree == course.credits
    assert result.credits_completed == course.credits
