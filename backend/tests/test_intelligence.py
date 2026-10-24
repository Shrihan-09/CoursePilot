"""Phase 7.2.4 course/professor intelligence: ranking evidence only, never truth.

Instructors, profiles and feedback are invented; students are fabricated.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete

from app.domain.course_ranking import CourseRankingEvidence
from app.domain.intelligence import AggregateState, Confidence, Dimension, IdentityMatch
from app.domain.modality import Modality
from app.models import CourseFeedback, InstructorExternalIdentity
from app.services.course_feedback import MIN_SAMPLE, aggregate
from app.services.external_sources import SOURCES, SourceNotPermitted, register, usable_sources
from app.services.instructor_identity import resolve
from app.services.scheduling.section_ranking import (
    LiveStatus,
    SectionCandidate,
    SectionPreferences,
    rank_sections,
)

from .test_multi_program import _session, requires_db, world  # noqa: F401

COURSE = "01:999:301"
NOW = dt.datetime(2026, 10, 19, tzinfo=dt.UTC)


# J. Professor evidence never makes an invalid course/section eligible ----
def test_course_ranking_has_no_professor_or_feedback_input():
    assert not {f for f in CourseRankingEvidence.model_fields
                if "professor" in f or "feedback" in f or "difficulty" in f or "rating" in f}


def test_glowing_professor_evidence_never_ranks_an_invalid_section():
    bad = SectionCandidate(index="90001", structurally_valid=False, invalid_reason="time_conflict",
                           live=LiveStatus.OPEN, instructor_identity=IdentityMatch.VERIFIED,
                           professor_preference=99)
    good = SectionCandidate(index="90002", structurally_valid=True)
    result = rank_sections([bad, good], SectionPreferences())
    assert [r.index for r in result.ranked] == ["90002"]
    assert result.excluded == {"90001": "time_conflict"}


def test_unverified_professor_evidence_is_ignored():
    a = SectionCandidate(index="90002", structurally_valid=True,
                         instructor_identity=IdentityMatch.UNVERIFIED, professor_preference=50)
    b = SectionCandidate(index="90001", structurally_valid=True)
    ranked = rank_sections([a, b], SectionPreferences()).ranked
    assert [r.index for r in ranked] == ["90001", "90002"]          # canonical, signal ignored
    assert not any(r.professor_evidence_used for r in ranked)


# M. Live status is separate from structural validity ---------------------
def test_closed_section_stays_valid_and_open_never_rescues_invalid():
    closed = SectionCandidate(index="90003", structurally_valid=True, live=LiveStatus.CLOSED)
    open_invalid = SectionCandidate(index="90004", structurally_valid=False, live=LiveStatus.OPEN)
    open_valid = SectionCandidate(index="90005", structurally_valid=True, live=LiveStatus.OPEN)
    result = rank_sections([closed, open_invalid, open_valid], SectionPreferences())
    assert [r.index for r in result.ranked] == ["90005", "90003"]
    assert result.ranked[1].live is LiveStatus.CLOSED and "90004" in result.excluded


def test_section_preferences_order_before_live_state():
    online_open = SectionCandidate(index="90006", structurally_valid=True,
                                   modality=Modality.ASYNCHRONOUS, live=LiveStatus.OPEN)
    in_person_closed = SectionCandidate(index="90007", structurally_valid=True,
                                        modality=Modality.IN_PERSON, live=LiveStatus.CLOSED)
    ranked = rank_sections([online_open, in_person_closed],
                           SectionPreferences(modality=Modality.IN_PERSON)).ranked
    assert [r.index for r in ranked] == ["90007", "90006"]
    assert ranked[1].preference_misses == ["modality"]


# External sources: permission is enforced, not assumed ------------------
def test_unpermitted_external_sources_cannot_be_registered():
    assert SOURCES["ratemyprofessors"].access.value == "not_permitted"
    assert SOURCES["reddit"].access.value == "pending_approval"

    class Scraper:
        source = "ratemyprofessors"

        def signals(self, instructor_key, external_id):
            return []

    with pytest.raises(SourceNotPermitted):
        register(Scraper())
    assert usable_sources() == []


# K. Ambiguous identity never attaches external ratings -------------------
@requires_db
def test_identity_attaches_only_through_a_verified_link(world):  # noqa: F811
    with _session() as session:
        session.execute(delete(InstructorExternalIdentity))
        assert resolve(session, "SMITH, J", "999", "reddit", ["p1"]) == (IdentityMatch.AMBIGUOUS,
                                                                        None)
        assert resolve(session, "SMITH, JOHN", "999", "reddit", ["p1", "p2"])[0] \
            is IdentityMatch.AMBIGUOUS
        assert resolve(session, "SMITH, JOHN", "999", "reddit", ["p1"]) == (
            IdentityMatch.UNVERIFIED, None)                     # plausible is not verified
        session.add(InstructorExternalIdentity(
            instructor_key="SMITH|JOHN", subject_code="999", source="reddit",
            external_id="p1", verification="verified_manual", verified_at=NOW))
        session.flush()
        assert resolve(session, "Smith, John", "999", "reddit", []) == (IdentityMatch.VERIFIED,
                                                                        "p1")
        # Same name in another department is another person until verified.
        assert resolve(session, "SMITH, JOHN", "998", "reddit", ["p1"])[0] \
            is not IdentityMatch.VERIFIED
        session.rollback()


# L. Missing difficulty stays unknown; aggregates protect individuals -----
@requires_db
def test_missing_or_thin_feedback_is_not_enough_data(world):  # noqa: F811
    with _session() as session:
        session.execute(delete(CourseFeedback))
        empty = aggregate(session, COURSE, Dimension.DIFFICULTY)
        assert empty.state is AggregateState.NOT_ENOUGH_DATA
        assert empty.wording == "Not enough data" and empty.median is None
        for i in range(MIN_SAMPLE - 1):
            session.add(CourseFeedback(student_id=world["a"] if i % 2 else world["b"],
                                       course_string=COURSE, term_code=f"2026{'19'[i % 2]}",
                                       instructor_key=f"K{i}", workload=1, difficulty=1,
                                       submitted_at=NOW))
        session.flush()
        thin = aggregate(session, COURSE, Dimension.DIFFICULTY)
        assert thin.state is AggregateState.NOT_ENOUGH_DATA
        assert thin.distribution is None and thin.median is None
        assert thin.provenance.sample_size == 0 and thin.provenance.confidence is Confidence.NONE
        assert "easy" not in thin.wording.lower() and "low" not in thin.wording.lower()
        session.rollback()


@requires_db
def test_enough_feedback_reports_what_students_said_with_provenance(world):  # noqa: F811
    with _session() as session:
        session.execute(delete(CourseFeedback))
        for i in range(MIN_SAMPLE):
            session.add(CourseFeedback(student_id=uuid.UUID(str(world["a" if i % 2 else "b"])),
                                       course_string=COURSE, term_code="20269",
                                       instructor_key=f"K{i}", workload=4, difficulty=2,
                                       submitted_at=NOW))
        session.flush()
        workload = aggregate(session, COURSE, Dimension.WORKLOAD)
        assert workload.state is AggregateState.AVAILABLE
        assert workload.wording == f"Higher reported workload ({MIN_SAMPLE} student reports)"
        assert workload.provenance.sample_size == MIN_SAMPLE
        assert workload.provenance.confidence is Confidence.LOW
        assert workload.distribution == {4: MIN_SAMPLE}
        session.rollback()
