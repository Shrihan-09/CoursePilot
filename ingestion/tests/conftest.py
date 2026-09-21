"""Ingestion test fixtures.

Two rules for this suite:

  * **No test touches the Rutgers network.** Everything runs off the archived
    fixture in `tests/fixtures/`. A suite that hits a live site is slow,
    flaky, and rude to the source.

  * **Database tests use a real SQL engine**, not mocks. Mocking a database
    would test our mocks rather than our constraints, and the constraints are
    the point (the unique key is what actually prevents duplicates).

SQLite stands in for Postgres here because no Postgres is available on this
machine. That is a real limitation, not an equivalence: SQLite enforces the
UNIQUE and CHECK constraints we care about, but it is NOT proof the schema
works on Postgres. See docs/DATA_SOURCES.md and the phase report.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from app.db.base import Base
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures"
SAMPLE = FIXTURE_DIR / "soc_courses_sample.json"
SECTION_SAMPLE = FIXTURE_DIR / "soc_sections_sample.json"
WINTER_SAMPLE = FIXTURE_DIR / "soc_winter_sections_sample.json"
CS_SAMPLE = FIXTURE_DIR / "soc_cs_courses_sample.json"
CS_REQUIREMENTS = FIXTURE_DIR / "cs_ba_requirements_26_27.json"

# The term the archived fixture payload was captured for (Fall 2026).
FIXTURE_TERM = "20269"
# Winter 2027 - the second term, used for multi-term tests.
WINTER_TERM = "20270"


@pytest.fixture(scope="session")
def sample_payload_bytes() -> bytes:
    """The raw archived SOC payload, exactly as bytes from disk.

    Returned as bytes rather than parsed objects so the parser is exercised on
    the same input shape it sees in production.
    """
    return SAMPLE.read_bytes()


@pytest.fixture(scope="session")
def sample_payload(sample_payload_bytes: bytes) -> list[dict]:
    return json.loads(sample_payload_bytes)


@pytest.fixture
def session() -> Session:
    """A fresh in-memory database per test, with the real schema."""
    engine = create_engine("sqlite://", future=True)

    # SQLite ignores foreign keys unless explicitly told not to. Without this
    # the tests would pass while a real FK violation went undetected.
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as s:
        yield s
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="session")
def section_payload_bytes() -> bytes:
    """Raw archived SOC bytes containing the section fixture.

    This file holds whole COURSE objects with their `sections` arrays, exactly
    as SOC returns them, so the same bytes feed both the course pipeline (to
    create the offerings) and the section pipeline.
    """
    return SECTION_SAMPLE.read_bytes()


@pytest.fixture(scope="session")
def section_payload(section_payload_bytes: bytes) -> list[dict]:
    return json.loads(section_payload_bytes)


@pytest.fixture
def section_cache_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A fetcher cache seeded with the section fixture under the archive name
    the fetcher derives from SocQuery(2026, '9', 'NB')."""
    import shutil

    shutil.copyfile(SECTION_SAMPLE, tmp_path / "soc_courses_2026_9_NB.json")
    return tmp_path


@pytest.fixture
def seeded_session(session, section_payload_bytes: bytes):
    """A session with the fixture's COURSES and OFFERINGS already loaded.

    Sections attach to offerings, so every section test needs the parent
    offerings to exist first. Loading them from the same payload the sections
    come from guarantees the two are consistent - which is exactly the
    guarantee the real pipeline relies on when it reads one archive.
    """
    from datetime import UTC, datetime

    from coursepilot_ingestion.loaders.postgres import CourseLoader
    from coursepilot_ingestion.normalizers.soc import SocNormalizer
    from coursepilot_ingestion.parsers.soc import SocParser
    from coursepilot_ingestion.schemas import IngestionStats

    raws = SocParser().parse(section_payload_bytes).courses
    normalizer = SocNormalizer(term_code=FIXTURE_TERM)
    courses = [normalizer.normalize(r) for r in raws]

    loader = CourseLoader(session)
    source = loader.get_or_create_source(
        kind="rutgers_official_api",
        url="https://classes.rutgers.edu/soc/api/courses.json?year=2026&term=9&campus=NB",
        content_hash="section-fixture-hash",
        retrieved_at=datetime.now(UTC),
        term_code=FIXTURE_TERM,
        academic_year="2026",
        archive_path=str(SECTION_SAMPLE),
        record_count=len(courses),
    )
    loader.load(courses, source, IngestionStats())
    session.commit()
    return session


@pytest.fixture(scope="session")
def winter_payload_bytes() -> bytes:
    """Raw archived SOC bytes for Winter 2027 (term 20270).

    Real records, selected so their registration indexes are ones actually
    reused from Fall 2026 - see scripts/make_winter_fixture.py.
    """
    return WINTER_SAMPLE.read_bytes()


@pytest.fixture(scope="session")
def cs_payload_bytes() -> bytes:
    """Real SOC records for the courses the CS major names, plus electives."""
    return CS_SAMPLE.read_bytes()


@pytest.fixture
def cs_session(session, cs_payload_bytes: bytes):
    """A session with the CS courses AND the curated CS requirement tree.

    Uses the real curated requirement definition, so these tests exercise the
    same data the production loader would build - not a simplified stand-in.
    """
    from datetime import UTC, datetime

    from coursepilot_ingestion.loaders.postgres import CourseLoader
    from coursepilot_ingestion.loaders.requirements import RequirementLoader
    from coursepilot_ingestion.normalizers.soc import SocNormalizer
    from coursepilot_ingestion.parsers.soc import SocParser
    from coursepilot_ingestion.schemas import IngestionStats

    courses = [
        SocNormalizer(term_code=FIXTURE_TERM).normalize(r)
        for r in SocParser().parse(cs_payload_bytes).courses
    ]
    loader = CourseLoader(session)
    source = loader.get_or_create_source(
        kind="rutgers_official_api",
        url="https://classes.rutgers.edu/soc/api/courses.json?year=2026&term=9&campus=NB",
        content_hash="cs-fixture-hash",
        retrieved_at=datetime.now(UTC),
        term_code=FIXTURE_TERM,
        academic_year="2026",
        archive_path=str(CS_SAMPLE),
        record_count=len(courses),
    )
    loader.load(courses, source, IngestionStats())
    session.flush()

    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    session.commit()
    return session
