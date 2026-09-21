"""PostgreSQL integration tests for section ingestion (Phase 2).

Reuses the database guards from `test_postgres_integration`: these TRUNCATE,
so they run only against a database whose name ends in `_test`.

Several checks here CANNOT be verified on SQLite and exist only in this file:

  * the `index_number ~ '^[0-9]+$'` and time-format CHECKs use PostgreSQL's
    regex operator and are declared `ddl_if(dialect="postgresql")`, so SQLite
    simply has no such constraint;
  * `ON DELETE CASCADE` enforcement through two levels of foreign key.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.models import (
    CourseOffering,
    CourseSection,
    SectionCrossListing,
    SectionInstructor,
    SectionMeeting,
)
from coursepilot_ingestion.loaders.postgres import CourseLoader
from coursepilot_ingestion.loaders.sections import SectionLoader
from coursepilot_ingestion.normalizers.sections import SocSectionNormalizer
from coursepilot_ingestion.normalizers.soc import SocNormalizer
from coursepilot_ingestion.parsers.sections import SocSectionParser
from coursepilot_ingestion.parsers.soc import SocParser
from coursepilot_ingestion.schemas import IngestionStats
from coursepilot_ingestion.section_schemas import SectionIngestionStats
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from tests.test_postgres_integration import DB_URL, pg_session, requires_postgres  # noqa: F401

pytestmark = pytest.mark.db

TERM = "20269"


def _seed(pg_session, payload: bytes) -> SectionIngestionStats:
    """Load the fixture's courses, then its sections, on PostgreSQL."""
    courses = [
        SocNormalizer(term_code=TERM).normalize(r)
        for r in SocParser().parse(payload).courses
    ]
    loader = CourseLoader(pg_session)
    source = loader.get_or_create_source(
        kind="rutgers_official_api",
        url="https://example.invalid/sections",
        content_hash="pg-sections",
        retrieved_at=datetime.now(UTC),
        term_code=TERM,
        academic_year="2026",
        archive_path=None,
        record_count=len(courses),
    )
    loader.load(courses, source, IngestionStats())

    sections = [
        SocSectionNormalizer(term_code=TERM).normalize(p, s)
        for p, s in SocSectionParser().parse(payload).sections
    ]
    stats = SectionIngestionStats()
    SectionLoader(pg_session).load(sections, source.id, stats, term_code=TERM)
    pg_session.commit()
    return stats


@requires_postgres
def test_section_tables_exist_on_postgres() -> None:
    engine = create_engine(DB_URL, future=True)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()

    assert {
        "course_section",
        "section_meeting",
        "section_instructor",
        "section_cross_listing",
    } <= tables


@requires_postgres
def test_section_load_on_postgres(pg_session, section_payload_bytes: bytes) -> None:
    stats = _seed(pg_session, section_payload_bytes)

    assert stats.sections_inserted == 15
    assert stats.unmatched_offering == 0
    assert pg_session.scalar(select(func.count()).select_from(CourseSection)) == 15
    assert pg_session.scalar(select(func.count()).select_from(SectionMeeting)) == 33
    assert pg_session.scalar(select(func.count()).select_from(SectionInstructor)) == 13
    assert pg_session.scalar(select(func.count()).select_from(SectionCrossListing)) == 2


@requires_postgres
def test_postgres_enforces_section_natural_key(
    pg_session, section_payload_bytes: bytes
) -> None:
    _seed(pg_session, section_payload_bytes)
    existing = pg_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "10052")
    )

    pg_session.add(
        CourseSection(
            offering_id=existing.offering_id,
            term_code=existing.term_code,
            index_number=existing.index_number,
            section_number="ZZ",
            campus_code="NB",
            open_status=True,
            source_id=existing.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_postgres_rejects_non_numeric_index(pg_session, section_payload_bytes: bytes) -> None:
    # The regex CHECK is PostgreSQL-only, so this is the ONLY place it can be
    # verified. SQLite has no such constraint by design.
    _seed(pg_session, section_payload_bytes)
    existing = pg_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "10052")
    )

    pg_session.add(
        CourseSection(
            offering_id=existing.offering_id,
            term_code=existing.term_code,
            index_number="ABC12",
            section_number="ZZ",
            campus_code="NB",
            open_status=True,
            source_id=existing.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_postgres_rejects_invalid_meeting_day(
    pg_session, section_payload_bytes: bytes
) -> None:
    _seed(pg_session, section_payload_bytes)
    section = pg_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "10052")
    )

    pg_session.add(
        SectionMeeting(
            section_id=section.id,
            meeting_index=99,
            meeting_day="X",
            meeting_mode_code="02",
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_postgres_rejects_half_populated_times(
    pg_session, section_payload_bytes: bytes
) -> None:
    _seed(pg_session, section_payload_bytes)
    section = pg_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "10052")
    )

    pg_session.add(
        SectionMeeting(
            section_id=section.id,
            meeting_index=98,
            start_time_military="1000",
            end_time_military=None,
            meeting_mode_code="02",
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_postgres_accepts_the_real_end_before_start_meeting(
    pg_session, section_payload_bytes: bytes
) -> None:
    # Three real Rutgers meetings have end <= start. The schema must NOT reject
    # them; doing so would discard authentic sections over a source quirk.
    _seed(pg_session, section_payload_bytes)

    section = pg_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "15777")
    )
    assert section is not None
    weird = [
        m
        for m in section.meetings
        if m.start_time_military
        and m.end_time_military
        and m.end_time_military <= m.start_time_military
    ]
    assert len(weird) >= 1


@requires_postgres
def test_postgres_stores_tba_meetings_as_null(
    pg_session, section_payload_bytes: bytes
) -> None:
    _seed(pg_session, section_payload_bytes)

    section = pg_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "10064")
    )
    assert section.meetings[0].meeting_day is None
    assert section.meetings[0].start_time_military is None


@requires_postgres
def test_postgres_section_idempotency(pg_session, section_payload_bytes: bytes) -> None:
    _seed(pg_session, section_payload_bytes)
    second = _seed(pg_session, section_payload_bytes)

    assert second.sections_inserted == 0
    assert pg_session.scalar(select(func.count()).select_from(CourseSection)) == 15
    assert pg_session.scalar(select(func.count()).select_from(SectionMeeting)) == 33
    assert pg_session.scalar(select(func.count()).select_from(SectionInstructor)) == 13


@requires_postgres
def test_postgres_cascade_deletes_section_children(
    pg_session, section_payload_bytes: bytes
) -> None:
    # Two levels of ON DELETE CASCADE, enforced by PostgreSQL itself.
    _seed(pg_session, section_payload_bytes)

    offering = pg_session.scalar(select(CourseOffering))
    pg_session.execute(
        text("DELETE FROM course_offering WHERE id = :i"), {"i": str(offering.id)}
    )
    pg_session.commit()

    assert pg_session.scalar(select(func.count()).select_from(CourseSection)) < 15
    orphans = pg_session.execute(
        text(
            "SELECT count(*) FROM section_meeting m "
            "LEFT JOIN course_section s ON s.id = m.section_id WHERE s.id IS NULL"
        )
    ).scalar()
    assert orphans == 0


# ==========================================================================
# Phase 2.5 - cross-term identity on PostgreSQL
# ==========================================================================
#
# Measured across five real terms (scripts/probe_cross_term_index.py):
# Rutgers reuses registration indexes aggressively, and a reused index almost
# always points at a DIFFERENT course. Index 10193 below is a real observed
# value: 01:070:111 sec 01 in Fall 2026, 01:013:130 sec 01 in Spring 2026.
#
# SQLite covers the same logic in test_cross_term_identity.py; these verify
# that PostgreSQL itself enforces it.

SPRING_2026 = "20261"
REUSED_INDEX = "10193"


def _second_term_offering(pg_session, term_code: str) -> CourseOffering:
    """Clone an existing offering into another term, reusing its course."""
    from app.models import DataSource

    existing = pg_session.scalars(select(CourseOffering)).first()
    src = DataSource(
        kind="rutgers_official_api",
        url="https://example.invalid/second-term",
        retrieved_at=datetime.now(UTC),
        content_hash=f"pg-{term_code}",
        term_code=term_code,
        academic_year=term_code[:4],
    )
    pg_session.add(src)
    pg_session.flush()

    off = CourseOffering(
        course_id=existing.course_id,
        term_code=term_code,
        campus_code="NB",
        source_id=src.id,
    )
    pg_session.add(off)
    pg_session.flush()
    return off


@requires_postgres
def test_pg_same_index_two_terms_allowed(pg_session, section_payload_bytes: bytes) -> None:
    """PostgreSQL must permit the same index_number in two different terms."""
    _seed(pg_session, section_payload_bytes)

    fall = pg_session.scalar(
        select(CourseSection).where(CourseSection.term_code == TERM)
    )
    spring_off = _second_term_offering(pg_session, SPRING_2026)

    pg_session.add(
        CourseSection(
            offering_id=spring_off.id,
            term_code=SPRING_2026,
            index_number=fall.index_number,   # deliberately the SAME index
            section_number="01",
            campus_code="NB",
            open_status=True,
            source_id=spring_off.source_id,
        )
    )
    pg_session.commit()

    rows = pg_session.scalars(
        select(CourseSection).where(CourseSection.index_number == fall.index_number)
    ).all()
    assert len(rows) == 2
    assert {r.term_code for r in rows} == {TERM, SPRING_2026}


@requires_postgres
def test_pg_duplicate_term_index_rejected(pg_session, section_payload_bytes: bytes) -> None:
    """uq_section_term_index must be enforced by PostgreSQL itself."""
    _seed(pg_session, section_payload_bytes)
    existing = pg_session.scalar(select(CourseSection))

    pg_session.add(
        CourseSection(
            offering_id=existing.offering_id,
            term_code=existing.term_code,
            index_number=existing.index_number,
            section_number="99",   # differs, so only term+index can catch it
            campus_code="NB",
            open_status=True,
            source_id=existing.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        pg_session.commit()


@requires_postgres
def test_pg_second_term_does_not_disturb_first(
    pg_session, section_payload_bytes: bytes
) -> None:
    """Adding a term must leave the existing term's rows untouched."""
    _seed(pg_session, section_payload_bytes)
    before = pg_session.scalar(
        select(func.count()).select_from(CourseSection).where(CourseSection.term_code == TERM)
    )

    spring_off = _second_term_offering(pg_session, SPRING_2026)
    pg_session.add(
        CourseSection(
            offering_id=spring_off.id,
            term_code=SPRING_2026,
            index_number="99999",
            section_number="01",
            campus_code="NB",
            open_status=True,
            source_id=spring_off.source_id,
        )
    )
    pg_session.commit()

    after = pg_session.scalar(
        select(func.count()).select_from(CourseSection).where(CourseSection.term_code == TERM)
    )
    assert before == after
    assert pg_session.scalar(
        select(func.count()).select_from(CourseSection).where(
            CourseSection.term_code == SPRING_2026
        )
    ) == 1
