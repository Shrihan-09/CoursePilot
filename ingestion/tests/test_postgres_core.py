"""PostgreSQL integration tests for SAS Core (Phase 4).

Reuses the database guards from `test_postgres_integration`: these TRUNCATE,
so they run only against a database whose name ends in `_test`.

Verifies what SQLite cannot prove for Core:
  * requirement_system is an OPEN set - 'core' needs no migration
  * the (requirement, course) unique constraint on eligibility
  * FK integrity from eligibility to both requirement and course
  * CASCADE from program_version through core requirements to eligibility
  * Major+Core sharing with real Numeric/Decimal credits
  * the real CS audit still works with Core loaded alongside it
"""

from __future__ import annotations

import pathlib
import tempfile
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.domain.audit import RequirementStatus
from app.models import (
    Course,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    Student,
    StudentCourse,
)
from app.services.audit import DegreeAuditEngine
from coursepilot_ingestion.loaders.postgres import CourseLoader
from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.normalizers.soc import SocNormalizer
from coursepilot_ingestion.parsers.soc import SocParser
from coursepilot_ingestion.pipelines.core import CoreIngestionPipeline
from coursepilot_ingestion.schemas import IngestionStats
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from tests.test_postgres_integration import DB_URL, pg_session, requires_postgres  # noqa: F401

pytestmark = pytest.mark.db

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
CS_REQUIREMENTS = FIXTURES / "cs_ba_requirements_26_27.json"
CORE_DEFINITION = FIXTURES / "sas_core_26_27.json"
TERM = "20269"


def _seed(pg_session, cs_payload_bytes: bytes, *, with_core: bool = True):
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
        content_hash="pg4-cs-courses",
        retrieved_at=datetime.now(UTC),
        term_code=TERM,
        academic_year="2026",
        archive_path=None,
        record_count=len(courses),
    )
    loader.load(courses, source, IngestionStats())
    pg_session.flush()
    RequirementLoader(pg_session).load_file(CS_REQUIREMENTS)
    pg_session.commit()

    if not with_core:
        return None

    archive = pathlib.Path(tempfile.mkdtemp()) / "soc_courses_2026_9_NB.json"
    archive.write_bytes(cs_payload_bytes)
    stats = CoreIngestionPipeline(pg_session).run(
        CORE_DEFINITION, archive, observed_term_code=TERM
    )
    pg_session.commit()
    return stats


def _enroll(pg_session, student, course_string, grade="A"):
    course = pg_session.scalar(
        select(Course).where(
            Course.course_string == course_string, Course.supplement_code == ""
        )
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
    pg_session.flush()
    return course


def _student(pg_session) -> Student:
    version = pg_session.scalar(select(ProgramVersion))
    student = Student(
        external_ref="pg4-core",
        catalog_year=version.catalog_year,
        program_version_id=version.id,
    )
    pg_session.add(student)
    pg_session.flush()
    return student


# --------------------------------------------------------------------------
# schema: no migration was needed
# --------------------------------------------------------------------------


@requires_postgres
def test_core_needs_no_new_tables(pg_session, cs_payload_bytes: bytes) -> None:
    """Core reuses the Phase 3 requirement tables entirely."""
    from sqlalchemy import create_engine, inspect

    engine = create_engine(DB_URL, future=True)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()

    assert not any("core" in t for t in tables), (
        "Core should reuse requirement tables, not add its own"
    )


@requires_postgres
def test_requirement_system_accepts_core_without_a_migration(
    pg_session, cs_payload_bytes: bytes
) -> None:
    """requirement_system is deliberately an OPEN set.

    If it carried a CHECK constraining it to known values, adding Core would
    have needed a schema migration.
    """
    _seed(pg_session, cs_payload_bytes)

    systems = {
        r.requirement_system for r in pg_session.scalars(select(Requirement)).all()
    }
    assert systems == {"major", "core"}


@requires_postgres
def test_core_loads_on_postgres(pg_session, cs_payload_bytes: bytes) -> None:
    stats = _seed(pg_session, cs_payload_bytes)

    assert stats.goals_defined == 13
    assert stats.requirements_inserted == 13
    assert stats.eligibility_inserted > 0
    assert stats.unresolved_courses == []

    core_count = pg_session.scalar(
        select(func.count())
        .select_from(Requirement)
        .where(Requirement.requirement_system == "core")
    )
    assert core_count == 13


# --------------------------------------------------------------------------
# constraints
# --------------------------------------------------------------------------


@requires_postgres
def test_duplicate_eligibility_rejected_by_postgres(
    pg_session, cs_payload_bytes: bytes
) -> None:
    _seed(pg_session, cs_payload_bytes)
    existing = pg_session.scalar(
        select(RequirementCourseOption)
        .join(Requirement, Requirement.id == RequirementCourseOption.requirement_id)
        .where(Requirement.requirement_system == "core")
    )

    # A TRUE duplicate now means same requirement, same course AND same
    # certifying category - the third column joined the constraint in Phase 4.1.
    pg_session.add(
        RequirementCourseOption(
            requirement_id=existing.requirement_id,
            course_id=existing.course_id,
            category=existing.category,
            source_id=existing.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_same_course_under_a_different_category_is_accepted(
    pg_session, cs_payload_bytes: bytes
) -> None:
    """The other half of the constraint change.

    01:198:111 is certified QQ and QR, both feeding CORE_QFR. Two rows are
    the point - collapsing them is what lost the information CORE_AH needs.
    """
    _seed(pg_session, cs_payload_bytes)
    existing = pg_session.scalar(
        select(RequirementCourseOption)
        .join(Requirement, Requirement.id == RequirementCourseOption.requirement_id)
        .where(Requirement.requirement_system == "core")
    )

    pg_session.add(
        RequirementCourseOption(
            requirement_id=existing.requirement_id,
            course_id=existing.course_id,
            category=existing.category + "_OTHER",
            source_id=existing.source_id,
        )
    )
    pg_session.commit()  # must not raise


@requires_postgres
def test_eligibility_rejects_a_bogus_course_fk(
    pg_session, cs_payload_bytes: bytes
) -> None:
    """Eligibility must point at a REAL course - no invented rows."""
    _seed(pg_session, cs_payload_bytes)
    existing = pg_session.scalar(
        select(RequirementCourseOption)
        .join(Requirement, Requirement.id == RequirementCourseOption.requirement_id)
        .where(Requirement.requirement_system == "core")
    )

    pg_session.add(
        RequirementCourseOption(
            requirement_id=existing.requirement_id,
            course_id=uuid.uuid4(),
            source_id=existing.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_duplicate_core_requirement_code_rejected(
    pg_session, cs_payload_bytes: bytes
) -> None:
    _seed(pg_session, cs_payload_bytes)
    existing = pg_session.scalar(
        select(Requirement).where(Requirement.code == "CORE_NS")
    )

    pg_session.add(
        Requirement(
            program_version_id=existing.program_version_id,
            code="CORE_NS",
            name="duplicate",
            requirement_type="credits",
            requirement_system="core",
            source_id=existing.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_deleting_the_version_cascades_through_core(
    pg_session, cs_payload_bytes: bytes
) -> None:
    _seed(pg_session, cs_payload_bytes)
    version = pg_session.scalar(select(ProgramVersion))
    assert pg_session.scalar(select(func.count()).select_from(RequirementCourseOption)) > 0

    pg_session.execute(
        text("DELETE FROM program_version WHERE id = :i"), {"i": version.id}
    )
    pg_session.commit()
    pg_session.expunge_all()

    assert pg_session.scalar(select(func.count()).select_from(Requirement)) == 0
    assert pg_session.scalar(select(func.count()).select_from(RequirementCourseOption)) == 0


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------


@requires_postgres
def test_core_provenance_is_recorded(pg_session, cs_payload_bytes: bytes) -> None:
    from app.models import DataSource

    stats = _seed(pg_session, cs_payload_bytes)

    source = pg_session.scalar(
        select(DataSource).where(DataSource.content_hash == stats.source_content_hash)
    )
    assert source is not None
    assert source.url.startswith("https://sasoue.rutgers.edu")
    assert source.academic_year == "2026-2027"
    assert source.retrieved_at is not None

    for req in pg_session.scalars(
        select(Requirement).where(Requirement.requirement_system == "core")
    ).all():
        assert req.source_prose
        assert req.curation_status == "curated_from_prose"


# --------------------------------------------------------------------------
# sharing with real Decimal credits
# --------------------------------------------------------------------------


@requires_postgres
def test_major_and_core_share_with_real_decimal_credits(
    pg_session, cs_payload_bytes: bytes
) -> None:
    """The whole point of Phase 3.75, on PostgreSQL with real Numeric."""
    _seed(pg_session, cs_payload_bytes)
    student = _student(pg_session)
    course = _enroll(pg_session, student, "01:198:111")  # CS_111 + QQ/QR
    pg_session.commit()

    result = DegreeAuditEngine(pg_session).audit(student)

    systems = {a.requirement_system for a in result.allocation}
    assert systems == {"major", "core"}

    assert isinstance(result.credits_applicable_to_degree, Decimal)
    assert result.credits_applicable_to_degree == course.credits
    assert result.credits_completed == course.credits


@requires_postgres
def test_sharing_does_not_double_count_over_many_courses(
    pg_session, cs_payload_bytes: bytes
) -> None:
    _seed(pg_session, cs_payload_bytes)
    student = _student(pg_session)

    expected = Decimal(0)
    for code in ("01:198:111", "01:640:151", "01:640:152", "01:013:120"):
        expected += _enroll(pg_session, student, code).credits
    pg_session.commit()

    result = DegreeAuditEngine(pg_session).audit(student)

    assert len(result.allocation) > 4, "some courses should count twice"
    assert result.credits_applicable_to_degree == expected


@requires_postgres
def test_core_credits_requirement_on_postgres(
    pg_session, cs_payload_bytes: bytes
) -> None:
    _seed(pg_session, cs_payload_bytes)
    student = _student(pg_session)
    _enroll(pg_session, student, "01:070:201")  # NS-only, 3 credits
    _enroll(pg_session, student, "01:070:212")  # NS-only, 3 credits
    pg_session.commit()

    result = DegreeAuditEngine(pg_session).audit(student)
    ns = next(
        r
        for root in result.requirements
        for area in root.children
        for r in ([area] + area.children)
        if r.requirement_code == "CORE_NS"
    )

    assert isinstance(ns.satisfied_credits, Decimal)
    assert ns.satisfied_credits == Decimal(6)
    assert ns.status is RequirementStatus.SATISFIED


@requires_postgres
def test_real_cs_audit_still_works_with_core_loaded(
    pg_session, cs_payload_bytes: bytes
) -> None:
    """Regression: adding Core must not disturb the major audit."""
    _seed(pg_session, cs_payload_bytes)
    student = _student(pg_session)
    for code in ("01:198:111", "01:198:112", "01:198:205", "01:198:206", "01:198:211"):
        _enroll(pg_session, student, code)
    pg_session.commit()

    result = DegreeAuditEngine(pg_session).audit(student)

    def find(code):
        def walk(rs):
            for r in rs:
                if r.requirement_code == code:
                    return r
                got = walk(r.children)
                if got:
                    return got
            return None

        return walk(result.requirements)

    assert find("CS_111").status is RequirementStatus.SATISFIED
    assert find("CS_CORE").satisfied_count == 5
    # 01:198:344 is still not taken, so the major is incomplete.
    assert find("CS_344").status is RequirementStatus.UNSATISFIED


@requires_postgres
def test_core_ingestion_idempotent_on_postgres(
    pg_session, cs_payload_bytes: bytes
) -> None:
    _seed(pg_session, cs_payload_bytes)
    before = pg_session.scalar(select(func.count()).select_from(RequirementCourseOption))

    archive = pathlib.Path(tempfile.mkdtemp()) / "soc_courses_2026_9_NB.json"
    archive.write_bytes(cs_payload_bytes)
    second = CoreIngestionPipeline(pg_session).run(CORE_DEFINITION, archive)
    pg_session.commit()

    assert second.requirements_inserted == 0
    assert second.eligibility_inserted == 0
    assert (
        pg_session.scalar(select(func.count()).select_from(RequirementCourseOption))
        == before
    )


@requires_postgres
def test_core_refuses_a_missing_target_program(
    pg_session, cs_payload_bytes: bytes
) -> None:
    """Core attaches to an existing program version. A missing target must
    raise rather than invent a program nobody is enrolled in."""
    _seed(pg_session, cs_payload_bytes, with_core=False)
    pg_session.execute(text("DELETE FROM program_version"))
    pg_session.commit()

    archive = pathlib.Path(tempfile.mkdtemp()) / "soc_courses_2026_9_NB.json"
    archive.write_bytes(cs_payload_bytes)

    with pytest.raises(ValueError, match="target program version not found"):
        CoreIngestionPipeline(pg_session).run(CORE_DEFINITION, archive)
