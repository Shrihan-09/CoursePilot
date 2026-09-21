"""Two terms in one database, through the real pipeline (Phase 2.75).

Phase 2.5 proved by measurement that Rutgers reuses registration indexes
across terms. Phase 2.75 loaded Fall 2026 and Winter 2027 into the live
PostgreSQL database and confirmed 21 shared indexes produced 21 pairs of
distinct sections, with zero collisions.

These tests turn that one-off manual verification into a regression guard, so
a future change to the section key fails here rather than silently corrupting
a second term's data.

Scope note: the *index collision* itself is exercised in
`test_cross_term_identity.py`, which uses the real observed pair
(index 10193 -> 01:070:111 in Fall 2026, 01:013:130 in Spring 2026). The two
fixtures here cannot collide naturally - the Fall fixture is a 15-section
subset, and Winter's real collisions are with sections outside it. Rather than
fabricate a collision, this file covers what the fixtures genuinely show:
two terms coexisting through the full pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models import Course, CourseOffering, CourseSection, DataSource
from coursepilot_ingestion.loaders.postgres import CourseLoader
from coursepilot_ingestion.loaders.sections import SectionLoader
from coursepilot_ingestion.normalizers.sections import SocSectionNormalizer
from coursepilot_ingestion.normalizers.soc import SocNormalizer
from coursepilot_ingestion.parsers.sections import SocSectionParser
from coursepilot_ingestion.parsers.soc import SocParser
from coursepilot_ingestion.schemas import IngestionStats
from coursepilot_ingestion.section_schemas import SectionIngestionStats
from sqlalchemy import func, select

FALL = "20269"
WINTER = "20270"


def _ingest(session, payload: bytes, term_code: str, content_hash: str):
    """Run courses then sections for one term, as the pipeline does."""
    courses = [
        SocNormalizer(term_code=term_code).normalize(r)
        for r in SocParser().parse(payload).courses
    ]
    course_loader = CourseLoader(session)
    source = course_loader.get_or_create_source(
        kind="rutgers_official_api",
        url=f"https://classes.rutgers.edu/soc/api/courses.json?term={term_code}",
        content_hash=content_hash,
        retrieved_at=datetime.now(UTC),
        term_code=term_code,
        academic_year=term_code[:4],
        archive_path=None,
        record_count=len(courses),
    )
    course_stats = IngestionStats()
    course_loader.load(courses, source, course_stats)

    parsed = SocSectionParser().parse(payload)
    normalizer = SocSectionNormalizer(term_code=term_code)
    sections = [normalizer.normalize(p, s) for p, s in parsed.sections]

    section_stats = SectionIngestionStats()
    SectionLoader(session).load(sections, source.id, section_stats, term_code=term_code)
    session.commit()
    return course_stats, section_stats


def _both_terms(session, fall_bytes: bytes, winter_bytes: bytes):
    _ingest(session, fall_bytes, FALL, "hash-fall")
    return _ingest(session, winter_bytes, WINTER, "hash-winter")


# --------------------------------------------------------------------------
# coexistence
# --------------------------------------------------------------------------


def test_two_terms_coexist(session, section_payload_bytes, winter_payload_bytes) -> None:
    _both_terms(session, section_payload_bytes, winter_payload_bytes)

    per_term = dict(
        session.execute(
            select(CourseSection.term_code, func.count()).group_by(CourseSection.term_code)
        ).all()
    )

    assert set(per_term) == {FALL, WINTER}
    assert per_term[FALL] == 15
    assert per_term[WINTER] == 8


def test_second_term_does_not_disturb_the_first(
    session, section_payload_bytes, winter_payload_bytes
) -> None:
    """The regression this phase exists to prevent."""
    _ingest(session, section_payload_bytes, FALL, "hash-fall")
    fall_before = session.scalar(
        select(func.count()).select_from(CourseSection).where(CourseSection.term_code == FALL)
    )
    fall_ids_before = set(
        session.scalars(
            select(CourseSection.id).where(CourseSection.term_code == FALL)
        ).all()
    )

    _ingest(session, winter_payload_bytes, WINTER, "hash-winter")

    fall_after = session.scalar(
        select(func.count()).select_from(CourseSection).where(CourseSection.term_code == FALL)
    )
    fall_ids_after = set(
        session.scalars(
            select(CourseSection.id).where(CourseSection.term_code == FALL)
        ).all()
    )

    assert fall_before == fall_after == 15
    # Not merely the same count - the same rows.
    assert fall_ids_before == fall_ids_after


def test_every_section_resolves_to_its_own_term_offering(
    session, section_payload_bytes, winter_payload_bytes
) -> None:
    """A section must never attach to another term's offering."""
    _both_terms(session, section_payload_bytes, winter_payload_bytes)

    for section in session.scalars(select(CourseSection)).all():
        assert section.term_code == section.offering.term_code


def test_no_unmatched_sections_in_either_term(
    session, section_payload_bytes, winter_payload_bytes
) -> None:
    _, winter_stats = _both_terms(session, section_payload_bytes, winter_payload_bytes)
    assert winter_stats.unmatched_offering == 0


# --------------------------------------------------------------------------
# course / offering behaviour across terms
# --------------------------------------------------------------------------


def test_a_course_in_both_terms_gets_two_offerings(
    session, section_payload_bytes, winter_payload_bytes
) -> None:
    """Course identity is term-independent; offerings are term-scoped."""
    _both_terms(session, section_payload_bytes, winter_payload_bytes)

    multi = [
        c
        for c in session.scalars(select(Course)).all()
        if len({o.term_code for o in c.offerings}) > 1
    ]

    for course in multi:
        terms = sorted(o.term_code for o in course.offerings)
        assert terms == [FALL, WINTER]
        # One offering per (term, campus) - never a duplicate for the same term.
        assert len(course.offerings) == len({(o.term_code, o.campus_code) for o in course.offerings})


def test_each_term_gets_its_own_data_source(
    session, section_payload_bytes, winter_payload_bytes
) -> None:
    """Provenance stays term-scoped: two terms, two source rows."""
    _both_terms(session, section_payload_bytes, winter_payload_bytes)

    sources = session.scalars(select(DataSource)).all()
    assert {s.term_code for s in sources} == {FALL, WINTER}
    assert {s.academic_year for s in sources} == {"2026", "2027"}


# --------------------------------------------------------------------------
# idempotency with two terms present
# --------------------------------------------------------------------------


def test_reingesting_one_term_leaves_both_intact(
    session, section_payload_bytes, winter_payload_bytes
) -> None:
    _both_terms(session, section_payload_bytes, winter_payload_bytes)

    def counts() -> tuple[int, int, int]:
        return (
            session.scalar(select(func.count()).select_from(Course)),
            session.scalar(select(func.count()).select_from(CourseOffering)),
            session.scalar(select(func.count()).select_from(CourseSection)),
        )

    before = counts()
    _ingest(session, winter_payload_bytes, WINTER, "hash-winter")
    assert counts() == before

    _ingest(session, section_payload_bytes, FALL, "hash-fall")
    assert counts() == before
