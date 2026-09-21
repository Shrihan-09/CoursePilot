"""PostgreSQL integration tests.

These tests TRUNCATE every table, so they run only against a dedicated test
database. See `_is_test_database` below - pointing them at the development
database would destroy ingested data, so that is refused rather than skipped
quietly.

Setup (once):

    docker compose up -d db
    docker exec coursepilot-db psql -U coursepilot -d postgres \
      -c "CREATE DATABASE coursepilot_test OWNER coursepilot;"
    cd backend && DATABASE_URL_SYNC=<...coursepilot_test> alembic upgrade head

Run:

    cd ingestion
    export TEST_DATABASE_URL=postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test
    pytest -m db

These exist because SQLite is NOT proof the schema works on Postgres. The two
differ in ways that matter to this schema specifically:

  * `Numeric` returns Decimal on Postgres but can round-trip through float on
    SQLite, so credit precision is only genuinely verified here.
  * `Uuid` is a native type on Postgres and CHAR(32) on SQLite.
  * CHECK and UNIQUE constraint enforcement details differ.
  * Postgres is case-sensitive about identifiers in ways SQLite is not.

Until these pass, "the pipeline works on Postgres" is an assumption.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session

from coursepilot_ingestion.loaders.postgres import CourseLoader
from coursepilot_ingestion.normalizers.soc import SocNormalizer
from coursepilot_ingestion.parsers.soc import SocParser
from coursepilot_ingestion.schemas import IngestionStats

pytestmark = pytest.mark.db

DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL_SYNC")


def _is_test_database(url: str) -> bool:
    """Guard against truncating a real database.

    These tests TRUNCATE. `DB_URL` falls back to `DATABASE_URL_SYNC`, which on
    a developer machine is the working database full of ingested Rutgers data.
    Requiring the database NAME to end in `_test` makes that mistake
    impossible instead of merely unlikely - and it is the kind of mistake you
    only make once, expensively.
    """
    name = urlsplit(url).path.lstrip("/")
    return name.endswith("_test")


def _postgres_available() -> bool:
    if not DB_URL or not DB_URL.startswith("postgresql"):
        return False
    if not _is_test_database(DB_URL):
        return False
    try:
        engine = create_engine(DB_URL, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _skip_reason() -> str:
    if not DB_URL or not DB_URL.startswith("postgresql"):
        return "no PostgreSQL configured; set TEST_DATABASE_URL (see module docstring)"
    if not _is_test_database(DB_URL):
        return (
            f"refusing to run: {urlsplit(DB_URL).path.lstrip('/')!r} is not a test database. "
            "These tests TRUNCATE every table. Set TEST_DATABASE_URL to a database "
            "whose name ends in '_test'."
        )
    return "PostgreSQL unreachable; is `docker compose up -d db` running and migrated?"


requires_postgres = pytest.mark.skipif(not _postgres_available(), reason=_skip_reason())


@pytest.fixture
def pg_session():
    engine = create_engine(DB_URL, future=True)
    with Session(engine) as session:
        # Start clean without dropping the schema the migration created.
        session.execute(
            text("TRUNCATE course_offering, course, subject, data_source RESTART IDENTITY CASCADE")
        )
        session.commit()
        yield session
        session.rollback()
    engine.dispose()


@requires_postgres
def test_migration_created_every_table() -> None:
    engine = create_engine(DB_URL, future=True)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()

    assert {"data_source", "subject", "course", "course_offering"} <= tables


@requires_postgres
def test_full_load_on_postgres(pg_session, sample_payload_bytes: bytes) -> None:
    normalizer = SocNormalizer(term_code="20269")
    courses = [normalizer.normalize(r) for r in SocParser().parse(sample_payload_bytes).courses]

    loader = CourseLoader(pg_session)
    source = loader.get_or_create_source(
        kind="rutgers_official_api",
        url="https://classes.rutgers.edu/soc/api/courses.json?year=2026&term=9&campus=NB",
        content_hash="pg-test-hash",
        retrieved_at=datetime.now(UTC),
        term_code="20269",
        academic_year="2026",
        archive_path=None,
        record_count=10,
    )
    stats = IngestionStats()
    loader.load(courses, source, stats)
    pg_session.commit()

    from app.models import Course, CourseOffering

    assert pg_session.scalar(select(func.count()).select_from(Course)) == 9
    assert pg_session.scalar(select(func.count()).select_from(CourseOffering)) == 10


@requires_postgres
def test_numeric_credits_round_trip_exactly(pg_session, sample_payload_bytes: bytes) -> None:
    # The reason Numeric was chosen over Float: 1.5 credits must come back as
    # exactly Decimal("1.5"), not 1.4999999999999998.
    normalizer = SocNormalizer(term_code="20269")
    courses = [normalizer.normalize(r) for r in SocParser().parse(sample_payload_bytes).courses]

    loader = CourseLoader(pg_session)
    source = loader.get_or_create_source(
        kind="rutgers_official_api",
        url="https://example.invalid",
        content_hash="pg-numeric",
        retrieved_at=datetime.now(UTC),
        term_code="20269",
        academic_year="2026",
        archive_path=None,
        record_count=10,
    )
    loader.load(courses, source, IngestionStats())
    pg_session.commit()

    from app.models import Course

    fractional = pg_session.scalar(select(Course).where(Course.course_string == "01:050:282"))
    assert isinstance(fractional.credits, Decimal)
    assert fractional.credits == Decimal("1.5")

    nulled = pg_session.scalar(select(Course).where(Course.course_string == "01:013:321"))
    assert nulled.credits is None


@requires_postgres
def test_unique_constraint_enforced_by_postgres(pg_session, sample_payload_bytes: bytes) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.models import Course

    normalizer = SocNormalizer(term_code="20269")
    courses = [normalizer.normalize(r) for r in SocParser().parse(sample_payload_bytes).courses]
    loader = CourseLoader(pg_session)
    source = loader.get_or_create_source(
        kind="rutgers_official_api",
        url="https://example.invalid",
        content_hash="pg-unique",
        retrieved_at=datetime.now(UTC),
        term_code="20269",
        academic_year="2026",
        archive_path=None,
        record_count=10,
    )
    loader.load(courses, source, IngestionStats())
    pg_session.commit()

    existing = pg_session.scalar(select(Course).where(Course.course_string == "01:013:120"))
    pg_session.add(
        Course(
            offering_unit_code=existing.offering_unit_code,
            subject_code=existing.subject_code,
            course_number=existing.course_number,
            supplement_code=existing.supplement_code,
            course_string=existing.course_string,
            title="DUPLICATE",
            subject_id=existing.subject_id,
            source_id=existing.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()
