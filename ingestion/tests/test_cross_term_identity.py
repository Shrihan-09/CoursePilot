"""Cross-term section identity (Phase 2.5).

The Phase 2 schema keys `course_section` on `(term_code, index_number)` rather
than on `index_number` alone. That was a defensive choice at the time, because
only Fall 2026 had been observed.

It is no longer defensive. Measured across five real Rutgers terms
(`scripts/probe_cross_term_index.py`, 2026-09-12):

    2026 Spring vs 2026 Fall   9,829 shared indexes, 9,330 -> a DIFFERENT course
    2025 Fall   vs 2026 Fall   8,664 shared indexes, 8,664 -> a DIFFERENT course

Registration indexes are reused aggressively and carry no cross-term meaning.
Keying on `index_number` alone would have silently collided ~83% of sections
on the second term ingested, attaching them to the wrong courses.

These tests pin that behaviour. The index values below are REAL observed
values, not invented ones:

    index 10193 = 01:070:111 section 01 in Fall 2026
                = 01:013:130 section 01 in Spring 2026

Everything here runs on SQLite; `test_postgres_sections.py` covers the
PostgreSQL-specific enforcement.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.models import Course, CourseOffering, CourseSection, DataSource, Subject
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

FALL_2026 = "20269"
SPRING_2026 = "20261"

# Real reused index: different course in each term.
REUSED_INDEX = "10193"


def _source(session, term_code: str, content_hash: str) -> DataSource:
    src = DataSource(
        kind="rutgers_official_api",
        url=f"https://classes.rutgers.edu/soc/api/courses.json?term={term_code}",
        retrieved_at=datetime.now(UTC),
        content_hash=content_hash,
        term_code=term_code,
        academic_year=term_code[:4],
    )
    session.add(src)
    session.flush()
    return src


def _course(session, source_id: uuid.UUID, subject_code: str, number: str) -> Course:
    subject = session.scalar(select(Subject).where(Subject.code == subject_code))
    if subject is None:
        subject = Subject(code=subject_code, offering_unit_code="01", source_id=source_id)
        session.add(subject)
        session.flush()

    course = Course(
        offering_unit_code="01",
        subject_code=subject_code,
        course_number=number,
        supplement_code="",
        course_string=f"01:{subject_code}:{number}",
        title=f"COURSE {subject_code}:{number}",
        subject_id=subject.id,
        source_id=source_id,
    )
    session.add(course)
    session.flush()
    return course


def _offering(session, course: Course, term_code: str, source_id: uuid.UUID) -> CourseOffering:
    off = CourseOffering(
        course_id=course.id, term_code=term_code, campus_code="NB", source_id=source_id
    )
    session.add(off)
    session.flush()
    return off


def _section(session, offering: CourseOffering, term_code: str, index_number: str, number: str):
    sec = CourseSection(
        offering_id=offering.id,
        term_code=term_code,
        index_number=index_number,
        section_number=number,
        campus_code="NB",
        open_status=True,
        source_id=offering.source_id,
    )
    session.add(sec)
    return sec


@pytest.fixture
def two_terms(session):
    """Fall 2026 and Spring 2026 offerings for the two real courses that
    share registration index 10193."""
    fall_src = _source(session, FALL_2026, "hash-fall-2026")
    spring_src = _source(session, SPRING_2026, "hash-spring-2026")

    fall_course = _course(session, fall_src.id, "070", "111")
    spring_course = _course(session, spring_src.id, "013", "130")

    return {
        "fall_offering": _offering(session, fall_course, FALL_2026, fall_src.id),
        "spring_offering": _offering(session, spring_course, SPRING_2026, spring_src.id),
    }


# --------------------------------------------------------------------------
# the core question
# --------------------------------------------------------------------------


def test_same_index_in_two_terms_is_allowed(session, two_terms) -> None:
    """The whole reason term_code is in the key.

    Real data: index 10193 belongs to 01:070:111 in Fall 2026 and to
    01:013:130 in Spring 2026. Both must coexist as distinct sections.
    """
    _section(session, two_terms["fall_offering"], FALL_2026, REUSED_INDEX, "01")
    _section(session, two_terms["spring_offering"], SPRING_2026, REUSED_INDEX, "01")
    session.commit()

    rows = session.scalars(
        select(CourseSection).where(CourseSection.index_number == REUSED_INDEX)
    ).all()

    assert len(rows) == 2
    assert {r.term_code for r in rows} == {FALL_2026, SPRING_2026}
    # And they point at genuinely different courses - the failure mode that
    # keying on index alone would have produced.
    assert rows[0].offering.course_id != rows[1].offering.course_id
    assert {r.offering.course.course_string for r in rows} == {"01:070:111", "01:013:130"}


def test_duplicate_term_and_index_is_rejected(session, two_terms) -> None:
    """Within one term the index must still be unique."""
    _section(session, two_terms["fall_offering"], FALL_2026, REUSED_INDEX, "01")
    session.commit()

    # A second section with the same (term, index) - different section number,
    # so only the term+index constraint can catch it.
    _section(session, two_terms["fall_offering"], FALL_2026, REUSED_INDEX, "02")

    with pytest.raises(IntegrityError):
        session.commit()


def test_terms_coexist_without_interfering(session, two_terms) -> None:
    """Loading a second term must not disturb the first."""
    _section(session, two_terms["fall_offering"], FALL_2026, "11111", "01")
    session.commit()
    fall_before = session.scalar(
        select(func.count()).select_from(CourseSection).where(CourseSection.term_code == FALL_2026)
    )

    _section(session, two_terms["spring_offering"], SPRING_2026, "22222", "01")
    session.commit()

    fall_after = session.scalar(
        select(func.count()).select_from(CourseSection).where(CourseSection.term_code == FALL_2026)
    )
    spring = session.scalar(
        select(func.count()).select_from(CourseSection).where(CourseSection.term_code == SPRING_2026)
    )

    assert fall_before == fall_after == 1
    assert spring == 1


def test_offering_is_term_scoped(session, two_terms) -> None:
    """One course in two terms yields two offerings, not one."""
    course = two_terms["fall_offering"].course
    src = session.scalar(select(DataSource).where(DataSource.term_code == SPRING_2026))
    _offering(session, course, SPRING_2026, src.id)
    session.commit()

    offerings = session.scalars(
        select(CourseOffering).where(CourseOffering.course_id == course.id)
    ).all()

    assert len(offerings) == 2
    assert {o.term_code for o in offerings} == {FALL_2026, SPRING_2026}


def test_section_number_may_repeat_across_terms(session, two_terms) -> None:
    """`section_number` is unique per offering, never globally - section '01'
    exists in every term for nearly every course."""
    _section(session, two_terms["fall_offering"], FALL_2026, "33333", "01")
    _section(session, two_terms["spring_offering"], SPRING_2026, "44444", "01")
    session.commit()

    rows = session.scalars(
        select(CourseSection).where(CourseSection.section_number == "01")
    ).all()
    assert len(rows) == 2
