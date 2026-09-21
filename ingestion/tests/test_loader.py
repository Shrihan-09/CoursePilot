"""Loader tests: database insertion, deduplication, and idempotency.

These run against a real SQL engine (in-memory SQLite) so the actual UNIQUE
and FOREIGN KEY constraints are exercised. Mocking the database here would
test the mock, not the schema, and the schema is what prevents duplicates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.models import Course, CourseOffering, DataSource, Subject
from coursepilot_ingestion.loaders.postgres import CourseLoader
from coursepilot_ingestion.normalizers.soc import SocNormalizer
from coursepilot_ingestion.parsers.soc import SocParser
from coursepilot_ingestion.schemas import IngestionStats, NormalizedCourse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

TERM = "20269"


def _fixture_courses(payload: bytes) -> list[NormalizedCourse]:
    raws = SocParser().parse(payload).courses
    normalizer = SocNormalizer(term_code=TERM)
    return [normalizer.normalize(r) for r in raws]


def _make_source(loader: CourseLoader, content_hash: str = "hash-a") -> DataSource:
    return loader.get_or_create_source(
        kind="rutgers_official_api",
        url="https://classes.rutgers.edu/soc/api/courses.json?year=2026&term=9&campus=NB",
        content_hash=content_hash,
        retrieved_at=datetime.now(UTC),
        term_code=TERM,
        academic_year="2026",
        archive_path="data/raw/sample.json",
        record_count=10,
    )


# --------------------------------------------------------------------------
# basic insertion
# --------------------------------------------------------------------------


def test_loads_the_fixture(session, sample_payload_bytes: bytes) -> None:
    loader = CourseLoader(session)
    stats = IngestionStats()
    courses = _fixture_courses(sample_payload_bytes)

    loader.load(courses, _make_source(loader), stats)
    session.commit()

    # 10 input records, but 16:400:513 appears twice (NB/OB) and collapses to
    # one course with two offerings. So 9 courses, 10 offerings.
    assert session.scalar(select(func.count()).select_from(Course)) == 9
    assert session.scalar(select(func.count()).select_from(CourseOffering)) == 10
    assert stats.courses_inserted == 9
    assert stats.offerings_inserted == 10


def test_campus_duplicate_becomes_one_course_two_offerings(
    session, sample_payload_bytes: bytes
) -> None:
    loader = CourseLoader(session)
    loader.load(_fixture_courses(sample_payload_bytes), _make_source(loader), IngestionStats())
    session.commit()

    course = session.scalar(select(Course).where(Course.course_string == "16:400:513"))
    assert course is not None

    campuses = {o.campus_code for o in course.offerings}
    assert campuses == {"NB", "OB"}


def test_supplement_duplicate_becomes_two_courses(session, sample_payload_bytes: bytes) -> None:
    # Lecture (4 credits) and lab (0 credits) share a courseString but are
    # different courses. Collapsing them would lose the lab entirely.
    loader = CourseLoader(session)
    loader.load(_fixture_courses(sample_payload_bytes), _make_source(loader), IngestionStats())
    session.commit()

    rows = session.scalars(select(Course).where(Course.course_string == "01:750:193")).all()

    assert len(rows) == 2
    assert {r.supplement_code for r in rows} == {"", "LB"}
    assert {r.credits for r in rows} == {Decimal(4), Decimal(0)}


def test_subjects_are_deduplicated(session, sample_payload_bytes: bytes) -> None:
    loader = CourseLoader(session)
    loader.load(_fixture_courses(sample_payload_bytes), _make_source(loader), IngestionStats())
    session.commit()

    # Several fixture courses share subject 013; it must be stored once.
    subject_013 = session.scalars(select(Subject).where(Subject.code == "013")).all()
    assert len(subject_013) == 1


# --------------------------------------------------------------------------
# idempotency — the core requirement
# --------------------------------------------------------------------------


def test_reingesting_identical_data_creates_no_duplicates(
    session, sample_payload_bytes: bytes
) -> None:
    loader = CourseLoader(session)
    courses = _fixture_courses(sample_payload_bytes)

    first = IngestionStats()
    loader.load(courses, _make_source(loader), first)
    session.commit()

    second = IngestionStats()
    loader.load(courses, _make_source(loader), second)
    session.commit()

    assert session.scalar(select(func.count()).select_from(Course)) == 9
    assert session.scalar(select(func.count()).select_from(CourseOffering)) == 10
    assert second.courses_inserted == 0
    assert second.offerings_inserted == 0


def test_unchanged_payload_reuses_the_source_row(session) -> None:
    # Provenance rows should not churn when nothing changed, or "when did this
    # fact change?" becomes unanswerable.
    loader = CourseLoader(session)
    a = _make_source(loader, content_hash="same-hash")
    session.commit()
    b = _make_source(loader, content_hash="same-hash")
    session.commit()

    assert a.id == b.id
    assert session.scalar(select(func.count()).select_from(DataSource)) == 1


def test_changed_payload_creates_a_new_source_row(session) -> None:
    loader = CourseLoader(session)
    a = _make_source(loader, content_hash="hash-a")
    session.commit()
    b = _make_source(loader, content_hash="hash-b")
    session.commit()

    assert a.id != b.id
    assert session.scalar(select(func.count()).select_from(DataSource)) == 2


def test_reingestion_updates_changed_fields(session, sample_payload_bytes: bytes) -> None:
    loader = CourseLoader(session)
    courses = _fixture_courses(sample_payload_bytes)
    loader.load(courses, _make_source(loader), IngestionStats())
    session.commit()

    target = next(c for c in courses if c.course_string == "01:013:120")
    updated = target.model_copy(update={"title": "LITERARY EGYPT REVISED"})

    stats = IngestionStats()
    loader.load([updated], _make_source(loader, "hash-b"), stats)
    session.commit()

    row = session.scalar(
        select(Course).where(Course.course_string == "01:013:120", Course.supplement_code == "")
    )
    assert row.title == "LITERARY EGYPT REVISED"
    assert stats.courses_updated == 1
    assert stats.courses_inserted == 0


def test_reingestion_never_erases_a_value_with_null(session, sample_payload_bytes: bytes) -> None:
    # A later payload that omits a field must not wipe data an earlier one
    # supplied. Otherwise a partial upstream response silently destroys data.
    loader = CourseLoader(session)
    courses = _fixture_courses(sample_payload_bytes)
    loader.load(courses, _make_source(loader), IngestionStats())
    session.commit()

    target = next(c for c in courses if c.course_string == "01:013:240")
    assert target.prereq_notes_raw is not None

    stripped = target.model_copy(update={"prereq_notes_raw": None})
    loader.load([stripped], _make_source(loader, "hash-b"), IngestionStats())
    session.commit()

    row = session.scalar(select(Course).where(Course.course_string == "01:013:240"))
    assert row.prereq_notes_raw is not None


# --------------------------------------------------------------------------
# constraints are the real guarantee
# --------------------------------------------------------------------------


def test_database_rejects_duplicate_natural_key(session, sample_payload_bytes: bytes) -> None:
    # Even if the application logic were buggy, the constraint must hold.
    loader = CourseLoader(session)
    courses = _fixture_courses(sample_payload_bytes)
    loader.load(courses, _make_source(loader), IngestionStats())
    session.commit()

    existing = session.scalar(select(Course).where(Course.course_string == "01:013:120"))
    session.add(
        Course(
            offering_unit_code=existing.offering_unit_code,
            subject_code=existing.subject_code,
            course_number=existing.course_number,
            supplement_code=existing.supplement_code,
            course_string=existing.course_string,
            title="SNEAKY DUPLICATE",
            subject_id=existing.subject_id,
            source_id=existing.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_database_rejects_negative_credits(session, sample_payload_bytes: bytes) -> None:
    loader = CourseLoader(session)
    courses = _fixture_courses(sample_payload_bytes)
    loader.load(courses, _make_source(loader), IngestionStats())
    session.commit()

    existing = session.scalar(select(Course).where(Course.course_string == "01:013:120"))
    existing.credits = Decimal(-5)
    with pytest.raises(IntegrityError):
        session.commit()
