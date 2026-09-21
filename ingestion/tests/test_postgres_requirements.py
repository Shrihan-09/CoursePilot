"""PostgreSQL integration tests for the Phase 3 requirement schema.

Reuses the database guards from `test_postgres_integration`: these TRUNCATE,
so they run only against a database whose name ends in `_test`.

Verifies what SQLite cannot prove:
  * FK enforcement across program -> version -> requirement -> option
  * two-level ON DELETE CASCADE down the requirement tree
  * self-referential CASCADE on the recursive `requirement.parent_id`
  * CHECK constraints on credit ranges and enum-like columns
  * Numeric precision on credit values
"""

from __future__ import annotations

import json
import pathlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.models import (
    Course,
    DataSource,
    Program,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    School,
    Student,
    StudentCourse,
)
from coursepilot_ingestion.loaders.postgres import CourseLoader
from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.normalizers.soc import SocNormalizer
from coursepilot_ingestion.parsers.soc import SocParser
from coursepilot_ingestion.schemas import IngestionStats
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from tests.test_postgres_integration import DB_URL, pg_session, requires_postgres  # noqa: F401

pytestmark = pytest.mark.db

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
CS_REQUIREMENTS = FIXTURES / "cs_ba_requirements_26_27.json"
TERM = "20269"


def _truncate_phase3(session) -> None:
    session.execute(
        text(
            "TRUNCATE student_course, student, requirement_course_option, requirement, "
            "program_version, program, school, catalog_course_entry RESTART IDENTITY CASCADE"
        )
    )
    session.commit()


def _seed(pg_session, cs_payload_bytes: bytes):
    """Load CS courses, then the curated requirement tree, on PostgreSQL."""
    _truncate_phase3(pg_session)
    courses = [
        SocNormalizer(term_code=TERM).normalize(r)
        for r in SocParser().parse(cs_payload_bytes).courses
    ]
    loader = CourseLoader(pg_session)
    source = loader.get_or_create_source(
        kind="rutgers_official_api",
        url="https://example.invalid/cs-courses",
        content_hash="pg-cs-courses",
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


@requires_postgres
def test_migration_created_phase3_tables() -> None:
    from sqlalchemy import create_engine, inspect

    engine = create_engine(DB_URL, future=True)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()

    assert {
        "school",
        "program",
        "program_version",
        "requirement",
        "requirement_course_option",
        "catalog_course_entry",
        "student",
        "student_course",
    } <= tables


@requires_postgres
def test_curated_requirements_load_on_postgres(pg_session, cs_payload_bytes: bytes) -> None:
    stats = _seed(pg_session, cs_payload_bytes)

    assert stats.unresolved_courses == []
    assert stats.requirements_inserted == 13
    assert pg_session.scalar(select(func.count()).select_from(Requirement)) == 13
    assert pg_session.scalar(select(func.count()).select_from(RequirementCourseOption)) > 0


@requires_postgres
def test_requirement_load_is_idempotent(pg_session, cs_payload_bytes: bytes) -> None:
    _seed(pg_session, cs_payload_bytes)
    before = pg_session.scalar(select(func.count()).select_from(Requirement))
    options_before = pg_session.scalar(
        select(func.count()).select_from(RequirementCourseOption)
    )

    second = RequirementLoader(pg_session).load_file(CS_REQUIREMENTS)
    pg_session.commit()

    assert second.requirements_inserted == 0
    assert second.eligibility_inserted == 0
    assert pg_session.scalar(select(func.count()).select_from(Requirement)) == before
    assert (
        pg_session.scalar(select(func.count()).select_from(RequirementCourseOption))
        == options_before
    )


# --------------------------------------------------------------------------
# constraints enforced by PostgreSQL itself
# --------------------------------------------------------------------------


@requires_postgres
def test_duplicate_program_version_rejected(pg_session, cs_payload_bytes: bytes) -> None:
    _seed(pg_session, cs_payload_bytes)
    version = pg_session.scalar(select(ProgramVersion))

    pg_session.add(
        ProgramVersion(
            program_id=version.program_id,
            catalog_year=version.catalog_year,
            source_id=version.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_duplicate_requirement_code_within_version_rejected(
    pg_session, cs_payload_bytes: bytes
) -> None:
    _seed(pg_session, cs_payload_bytes)
    existing = pg_session.scalar(select(Requirement).where(Requirement.code == "CS_CORE"))

    pg_session.add(
        Requirement(
            program_version_id=existing.program_version_id,
            code="CS_CORE",
            name="duplicate",
            requirement_type="all_of",
            source_id=existing.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_unknown_requirement_type_rejected(pg_session, cs_payload_bytes: bytes) -> None:
    """The CHECK constraint must reject a type the engine cannot evaluate."""
    _seed(pg_session, cs_payload_bytes)
    existing = pg_session.scalar(select(Requirement))

    pg_session.add(
        Requirement(
            program_version_id=existing.program_version_id,
            code="BOGUS",
            name="bogus",
            requirement_type="whatever_i_want",
            source_id=existing.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_inverted_credit_range_rejected(pg_session, cs_payload_bytes: bytes) -> None:
    _seed(pg_session, cs_payload_bytes)
    version = pg_session.scalar(select(ProgramVersion))
    version.total_credits_min = Decimal(60)
    version.total_credits_max = Decimal(10)

    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_completed_course_without_grade_rejected(pg_session, cs_payload_bytes: bytes) -> None:
    _seed(pg_session, cs_payload_bytes)
    version = pg_session.scalar(select(ProgramVersion))
    course = pg_session.scalar(select(Course))

    student = Student(
        external_ref="pg-grade-test",
        catalog_year=version.catalog_year,
        program_version_id=version.id,
    )
    pg_session.add(student)
    pg_session.flush()
    pg_session.add(
        StudentCourse(
            student_id=student.id,
            course_id=course.id,
            term_code=TERM,
            status="completed",
            grade=None,  # violates completed_requires_grade
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_requirement_option_needs_a_real_course(pg_session, cs_payload_bytes: bytes) -> None:
    """FK enforcement: eligibility cannot point at a course that does not exist."""
    _seed(pg_session, cs_payload_bytes)
    req = pg_session.scalar(select(Requirement))

    pg_session.add(
        RequirementCourseOption(
            requirement_id=req.id,
            course_id=uuid.uuid4(),  # no such course
            source_id=req.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


# --------------------------------------------------------------------------
# cascade behaviour
# --------------------------------------------------------------------------


@requires_postgres
def test_deleting_a_version_cascades_to_requirements_and_options(
    pg_session, cs_payload_bytes: bytes
) -> None:
    _seed(pg_session, cs_payload_bytes)
    version = pg_session.scalar(select(ProgramVersion))

    pg_session.delete(version)
    pg_session.commit()

    assert pg_session.scalar(select(func.count()).select_from(Requirement)) == 0
    assert pg_session.scalar(select(func.count()).select_from(RequirementCourseOption)) == 0
    # The program itself survives - it is catalog-year independent.
    assert pg_session.scalar(select(func.count()).select_from(Program)) == 1


@requires_postgres
def test_deleting_a_parent_requirement_cascades_to_children(
    pg_session, cs_payload_bytes: bytes
) -> None:
    """The recursive FK must cascade down the tree."""
    _seed(pg_session, cs_payload_bytes)
    core = pg_session.scalar(select(Requirement).where(Requirement.code == "CS_CORE"))
    # Capture the id before deleting: the ORM object becomes stale afterwards
    # and touching it would raise ObjectDeletedError instead of testing the FK.
    core_id = core.id
    total_before = pg_session.scalar(select(func.count()).select_from(Requirement))
    children = pg_session.scalar(
        select(func.count()).select_from(Requirement).where(Requirement.parent_id == core_id)
    )
    assert children == 6

    # Raw SQL so PostgreSQL's ON DELETE CASCADE does the work, not SQLAlchemy's
    # ORM-side cascade - the point is that the DATABASE enforces it.
    pg_session.execute(text("DELETE FROM requirement WHERE id = :i"), {"i": core_id})
    pg_session.commit()
    pg_session.expunge_all()

    assert (
        pg_session.scalar(
            select(func.count()).select_from(Requirement).where(Requirement.parent_id == core_id)
        )
        == 0
    )
    # parent + 6 children gone
    assert pg_session.scalar(select(func.count()).select_from(Requirement)) == total_before - 7


@requires_postgres
def test_credits_round_trip_exactly_on_postgres(pg_session, cs_payload_bytes: bytes) -> None:
    _seed(pg_session, cs_payload_bytes)
    version = pg_session.scalar(select(ProgramVersion))

    assert isinstance(version.total_credits_min, Decimal)
    assert version.total_credits_min == Decimal("51.0")
    assert version.total_credits_max == Decimal("55.0")


@requires_postgres
def test_audit_runs_on_postgres(pg_session, cs_payload_bytes: bytes) -> None:
    """End-to-end: real courses + curated requirements + audit, on PostgreSQL."""
    from app.domain.audit import RequirementStatus
    from app.services.audit import DegreeAuditEngine

    _seed(pg_session, cs_payload_bytes)
    version = pg_session.scalar(select(ProgramVersion))
    student = Student(
        external_ref="pg-audit",
        catalog_year=version.catalog_year,
        program_version_id=version.id,
    )
    pg_session.add(student)
    pg_session.flush()

    for code in ["01:640:151", "01:640:152", "01:640:250"]:
        course = pg_session.scalar(
            select(Course).where(Course.course_string == code, Course.supplement_code == "")
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

    math = next(
        r
        for root in result.requirements
        for r in root.children
        if r.requirement_code == "CS_MATH"
    )
    assert math.status is RequirementStatus.SATISFIED
    assert result.credits_completed == Decimal(11)
