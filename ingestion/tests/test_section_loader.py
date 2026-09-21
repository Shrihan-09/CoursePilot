"""Section loader tests: course association, insertion, idempotency, constraints.

Run against a real SQL engine so the actual UNIQUE and FK constraints are
exercised. Mocking the database would test the mock, not the schema.
"""

from __future__ import annotations

import pytest
from app.models import (
    Course,
    CourseOffering,
    CourseSection,
    SectionCrossListing,
    SectionInstructor,
    SectionMeeting,
)
from coursepilot_ingestion.loaders.sections import SectionLoader
from coursepilot_ingestion.normalizers.sections import SocSectionNormalizer
from coursepilot_ingestion.parsers.sections import SocSectionParser
from coursepilot_ingestion.section_schemas import NormalizedSection, SectionIngestionStats
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

TERM = "20269"


def _sections(payload: bytes) -> list[NormalizedSection]:
    parsed = SocSectionParser().parse(payload)
    n = SocSectionNormalizer(term_code=TERM)
    return [n.normalize(p, s) for p, s in parsed.sections]


def _source_id(session):
    from app.models import DataSource

    return session.scalar(select(DataSource.id))


def _load(session, sections, stats=None) -> SectionIngestionStats:
    stats = stats or SectionIngestionStats()
    SectionLoader(session).load(sections, _source_id(session), stats, term_code=TERM)
    session.commit()
    return stats


# --------------------------------------------------------------------------
# basic insertion
# --------------------------------------------------------------------------


def test_loads_the_fixture(seeded_session, section_payload_bytes: bytes) -> None:
    stats = _load(seeded_session, _sections(section_payload_bytes))

    assert stats.sections_inserted == 15
    assert stats.unmatched_offering == 0
    assert seeded_session.scalar(select(func.count()).select_from(CourseSection)) == 15
    assert seeded_session.scalar(select(func.count()).select_from(SectionMeeting)) == 33
    assert seeded_session.scalar(select(func.count()).select_from(SectionInstructor)) == 13
    assert seeded_session.scalar(select(func.count()).select_from(SectionCrossListing)) == 2


def test_every_section_is_attached_to_an_offering(
    seeded_session, section_payload_bytes: bytes
) -> None:
    _load(seeded_session, _sections(section_payload_bytes))

    for section in seeded_session.scalars(select(CourseSection)).all():
        assert section.offering_id is not None
        assert section.offering.course is not None


# --------------------------------------------------------------------------
# course association - by identifier, never by title
# --------------------------------------------------------------------------


def test_supplement_pair_sections_go_to_different_courses(
    seeded_session, section_payload_bytes: bytes
) -> None:
    # 01:750:193 lecture ('') and lab ('LB') are DIFFERENT courses that share a
    # courseString. Their sections must not be merged onto one course.
    _load(seeded_session, _sections(section_payload_bytes))

    lecture = seeded_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "13352")
    )
    lab = seeded_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "13361")
    )

    assert lecture.offering.course.supplement_code == ""
    assert lab.offering.course.supplement_code == "LB"
    assert lecture.offering.course_id != lab.offering.course_id


def test_campus_pair_sections_share_a_course_but_not_an_offering(
    seeded_session, section_payload_bytes: bytes
) -> None:
    # 16:400:513 is ONE course offered at NB and OB. Each campus has its own
    # offering and its own section.
    _load(seeded_session, _sections(section_payload_bytes))

    nb = seeded_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "19370")
    )
    ob = seeded_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "19371")
    )

    assert nb.offering.course_id == ob.offering.course_id
    assert nb.offering_id != ob.offering_id
    assert {nb.campus_code, ob.campus_code} == {"NB", "OB"}


def test_unmatched_sections_are_reported_not_discarded(
    seeded_session, section_payload_bytes: bytes
) -> None:
    # A section whose parent course was never ingested must be counted and
    # named - never silently dropped, and never used to fabricate a course.
    sections = _sections(section_payload_bytes)
    orphan = sections[0].model_copy(
        update={"index_number": "99999", "subject_code": "999", "section_number": "99"}
    )

    stats = _load(seeded_session, [*sections, orphan])

    assert stats.unmatched_offering == 1
    assert any("99999" in d for d in stats.unmatched_details)
    assert stats.sections_inserted == 15  # the orphan was not inserted
    # And no course was invented for it.
    assert seeded_session.scalar(
        select(func.count()).select_from(Course).where(Course.subject_code == "999")
    ) == 0


# --------------------------------------------------------------------------
# child rows
# --------------------------------------------------------------------------


def test_meeting_ordinals_are_preserved(seeded_session, section_payload_bytes: bytes) -> None:
    _load(seeded_session, _sections(section_payload_bytes))

    section = seeded_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "15777")
    )
    assert [m.meeting_index for m in section.meetings] == [0, 1, 2, 3, 4]


def test_tba_meetings_store_nulls(seeded_session, section_payload_bytes: bytes) -> None:
    _load(seeded_session, _sections(section_payload_bytes))

    section = seeded_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "10064")
    )
    meeting = section.meetings[0]

    assert meeting.meeting_day is None
    assert meeting.start_time_military is None
    assert meeting.is_tba is True


def test_duplicate_instructor_names_both_stored(
    seeded_session, section_payload_bytes: bytes
) -> None:
    _load(seeded_session, _sections(section_payload_bytes))

    section = seeded_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "12326")
    )

    assert len(section.instructors) == 2
    assert section.instructors[0].name == section.instructors[1].name


def test_deleting_a_section_cascades_to_children(
    seeded_session, section_payload_bytes: bytes
) -> None:
    _load(seeded_session, _sections(section_payload_bytes))

    section = seeded_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "15777")
    )
    seeded_session.delete(section)
    seeded_session.commit()

    assert seeded_session.scalar(select(func.count()).select_from(CourseSection)) == 14
    assert seeded_session.scalar(select(func.count()).select_from(SectionMeeting)) == 28


# --------------------------------------------------------------------------
# idempotency
# --------------------------------------------------------------------------


def test_reingesting_creates_no_duplicates(
    seeded_session, section_payload_bytes: bytes
) -> None:
    sections = _sections(section_payload_bytes)

    _load(seeded_session, sections)
    second = _load(seeded_session, sections)

    assert second.sections_inserted == 0
    assert seeded_session.scalar(select(func.count()).select_from(CourseSection)) == 15
    # Children are replaced, not appended: counts must stay identical.
    assert seeded_session.scalar(select(func.count()).select_from(SectionMeeting)) == 33
    assert seeded_session.scalar(select(func.count()).select_from(SectionInstructor)) == 13
    assert seeded_session.scalar(select(func.count()).select_from(SectionCrossListing)) == 2


def test_reingestion_updates_changed_fields(
    seeded_session, section_payload_bytes: bytes
) -> None:
    sections = _sections(section_payload_bytes)
    _load(seeded_session, sections)

    target = next(s for s in sections if s.index_number == "10052")
    flipped = target.model_copy(update={"open_status": False, "open_status_text": "CLOSED"})

    stats = _load(seeded_session, [flipped])

    row = seeded_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "10052")
    )
    assert row.open_status is False
    assert stats.sections_updated == 1
    assert stats.sections_inserted == 0


def test_open_status_false_is_not_treated_as_missing(
    seeded_session, section_payload_bytes: bytes
) -> None:
    # A naive "skip falsy values" upsert would refuse to write False, leaving
    # a closed section permanently marked OPEN.
    sections = _sections(section_payload_bytes)
    target = next(s for s in sections if s.index_number == "10052")

    _load(seeded_session, [target.model_copy(update={"open_status": True})])
    _load(seeded_session, [target.model_copy(update={"open_status": False})])

    row = seeded_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "10052")
    )
    assert row.open_status is False


def test_shrinking_meeting_list_leaves_no_orphans(
    seeded_session, section_payload_bytes: bytes
) -> None:
    # Children are replaced rather than upserted precisely so a shortened list
    # cannot leave stale rows behind.
    sections = _sections(section_payload_bytes)
    _load(seeded_session, sections)

    target = next(s for s in sections if s.index_number == "15777")
    assert len(target.meetings) == 5
    trimmed = target.model_copy(update={"meetings": target.meetings[:2]})

    _load(seeded_session, [trimmed])

    row = seeded_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "15777")
    )
    assert len(row.meetings) == 2
    assert seeded_session.scalar(select(func.count()).select_from(SectionMeeting)) == 30


# --------------------------------------------------------------------------
# constraints are the real guarantee
# --------------------------------------------------------------------------


def test_database_rejects_duplicate_term_index(
    seeded_session, section_payload_bytes: bytes
) -> None:
    _load(seeded_session, _sections(section_payload_bytes))

    existing = seeded_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "10052")
    )
    seeded_session.add(
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
        seeded_session.commit()


def test_database_rejects_duplicate_section_number_in_offering(
    seeded_session, section_payload_bytes: bytes
) -> None:
    _load(seeded_session, _sections(section_payload_bytes))

    existing = seeded_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "10052")
    )
    seeded_session.add(
        CourseSection(
            offering_id=existing.offering_id,
            term_code=existing.term_code,
            index_number="99999",
            section_number=existing.section_number,
            campus_code="NB",
            open_status=True,
            source_id=existing.source_id,
        )
    )
    with pytest.raises(IntegrityError):
        seeded_session.commit()


def test_database_rejects_duplicate_meeting_ordinal(
    seeded_session, section_payload_bytes: bytes
) -> None:
    _load(seeded_session, _sections(section_payload_bytes))

    section = seeded_session.scalar(
        select(CourseSection).where(CourseSection.index_number == "10052")
    )
    seeded_session.add(
        SectionMeeting(section_id=section.id, meeting_index=0, meeting_mode_code="02")
    )
    with pytest.raises(IntegrityError):
        seeded_session.commit()


def test_database_rejects_orphan_section(seeded_session) -> None:
    import uuid

    seeded_session.add(
        CourseSection(
            offering_id=uuid.uuid4(),
            term_code=TERM,
            index_number="12345",
            section_number="01",
            campus_code="NB",
            open_status=True,
            source_id=_source_id(seeded_session),
        )
    )
    with pytest.raises(IntegrityError):
        seeded_session.commit()


def test_offering_count_is_unchanged_by_section_loading(
    seeded_session, section_payload_bytes: bytes
) -> None:
    before = seeded_session.scalar(select(func.count()).select_from(CourseOffering))
    _load(seeded_session, _sections(section_payload_bytes))
    after = seeded_session.scalar(select(func.count()).select_from(CourseOffering))

    # Section ingestion must never create or destroy courses/offerings.
    assert before == after


def test_meeting_campus_is_recorded_per_meeting(
    seeded_session, section_payload_bytes: bytes
) -> None:
    """830 real sections meet on more than one campus (usually a physical
    room plus ONLINE). Campus must therefore live on the MEETING, not only on
    the section - otherwise a hybrid section looks like it never moves, and
    travel-time feasibility becomes impossible to compute later.
    """
    _load(seeded_session, _sections(section_payload_bytes))

    spanning = [
        s
        for s in seeded_session.scalars(select(CourseSection)).all()
        if len({m.campus_name for m in s.meetings if m.campus_name}) > 1
    ]

    assert spanning, "fixture should contain at least one multi-campus section"
    campuses = {m.campus_name for m in spanning[0].meetings if m.campus_name}
    assert len(campuses) > 1
