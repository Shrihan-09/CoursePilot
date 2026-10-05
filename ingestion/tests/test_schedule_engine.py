"""The Schedule Engine through the REAL ingestion pipeline (Phase 6.6).

Fixtures are whole, unmodified Rutgers SOC course records copied from the
archived NB payloads by scripts/make_schedule_fixture.py:

    soc_schedule_fall2026_sample.json    30 course records, 486 sections (20269)
    soc_schedule_summer2026_sample.json   4 course records, with sessionDates (20267)

loaded by the production course AND section pipelines, with the real
curated CS program. Where a case has no real example, the test says
SYNTHETIC and names what it imitates.
"""

from __future__ import annotations

import hashlib
import json
import random
import uuid

import pytest
from app.db.base import Base
from app.domain.schedule import (
    AvailabilityFreshness,
    AvailabilityState,
    MeetingKind,
    Reason,
    RestrictionOutcome,
    SchedulePreferences,
    ScheduleStatus,
)
from app.models import (
    Course,
    CourseSection,
    ProgramVersion,
    SectionRestriction,
    Student,
    StudentCourse,
)
from app.services.scheduling.service import InvalidScheduleRequest, generate_schedule
from sqlalchemy import func, select

from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import CS_REQUIREMENTS, FIXTURE_DIR

FALL, SUMMER = "20269", "20267"
CS_RECORD = [("01:198:111", "20259"), ("01:198:112", "20261"), ("01:198:205", "20259"),
             ("01:198:206", "20261"), ("01:198:211", "20261"), ("01:640:151", "20259"),
             ("01:640:152", "20261"), ("01:640:250", "20261")]


def _load(session, tmp_path, fixture, year, term):
    query = SocQuery(year=year, term=term, campus="NB")
    folder = tmp_path / f"{year}{term}"
    folder.mkdir(exist_ok=True)
    target = folder / f"soc_courses_{year}_{term}_NB.json"
    target.write_bytes((FIXTURE_DIR / fixture).read_bytes())
    CourseIngestionPipeline(session, folder).run(query, coverage="complete")
    stats = SectionIngestionPipeline(session, folder).run(query)
    assert stats.unmatched_offering == 0 and stats.validation_failed == 0
    return stats


@pytest.fixture
def world(session, tmp_path):
    _load(session, tmp_path, "soc_schedule_fall2026_sample.json", 2026, "9")
    _load(session, tmp_path, "soc_schedule_summer2026_sample.json", 2026, "7")
    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    session.commit()
    return session


def _student(session, records=CS_RECORD):
    version = session.scalar(select(ProgramVersion))
    student = Student(external_ref=f"s-{uuid.uuid4().hex[:8]}", catalog_year=version.catalog_year,
                      program_version_id=version.id)
    session.add(student)
    session.flush()
    for key, term in records:
        course = session.scalar(select(Course).where(Course.course_string == key,
                                                     Course.supplement_code == ""))
        session.add(StudentCourse(student_id=student.id, course_id=course.id, term_code=term,
                                  status="completed", grade="A", credits_earned=course.credits))
    session.commit()
    return student


def _gen(session, student, courses, term=FALL, **kw):
    return generate_schedule(session, student, term_code=term, courses=courses, **kw)


def _overlap(a, b, buffer=0):
    """An INDEPENDENT oracle (not the engine's code): do two meetings clash?"""
    if a.kind is not MeetingKind.TIMED or b.kind is not MeetingKind.TIMED or a.day != b.day:
        return False
    if None not in (a.start_date, a.end_date, b.start_date, b.end_date) and (
            a.end_date < b.start_date or b.end_date < a.start_date):
        return False
    return max(a.start_minute, b.start_minute) < min(a.end_minute, b.end_minute) + buffer


def _verify(result, buffer=0):
    """Invariants of every option: one choice per requested slot, nothing
    replaced, no clash between ANY meetings of different choices, no
    cross-listed pair, no failed restriction."""
    for option in result.options:
        primaries = sorted(c.course for c in option.choices if c.component == "primary")
        assert primaries == sorted(set(result.requested_courses)), "course replaced or missing"
        indexes = [c.index_number for c in option.choices]
        assert len(indexes) == len(set(indexes))
        for i, a in enumerate(option.choices):
            assert a.restriction.outcome is not RestrictionOutcome.NOT_SATISFIED
            assert a.availability.freshness is AvailabilityFreshness.ARCHIVED
            for b in option.choices[i + 1:]:
                assert b.index_number not in a.cross_listed_indexes
                for x in a.meetings:
                    for y in b.meetings:
                        assert not _overlap(x, y, buffer), (a.index_number, b.index_number)
        keys = [(o.score.needs_confirmation, o.score.unknown_time_sections,
                 o.score.preference_misses, o.score.class_days, o.score.gap_minutes)
                for o in result.options]
        assert keys == sorted(keys)
        assert [o.rank for o in result.options] == list(range(1, len(result.options) + 1))


# ==========================================================================
# ingestion: the fields Phase 6.6 needed
# ==========================================================================


def test_session_dates_and_restrictions_are_stored(world) -> None:
    summer = world.scalars(select(CourseSection).where(CourseSection.term_code == SUMMER)).all()
    assert summer and all(s.session_start_date and s.session_end_date for s in summer)
    s386 = next(s for s in summer if s.index_number == "07538")
    assert (s386.session_dates_raw, str(s386.session_start_date), str(s386.session_end_date)) == \
        ("07/06/2026 - 07/31/2026", "2026-07-06", "2026-07-31")
    fall = world.scalars(select(CourseSection).where(CourseSection.term_code == FALL)).all()
    assert all(s.session_start_date is None for s in fall)          # never invented
    kinds = dict(world.execute(select(SectionRestriction.kind, func.count())
                               .group_by(SectionRestriction.kind)).all())
    assert kinds.get("major") and kinds.get("unit")
    section = world.scalar(select(CourseSection).where(CourseSection.index_number == "11592"))
    assert [(r.kind, r.code) for r in section.restrictions][:1] == [("major", "198")]


def test_section_reingest_is_idempotent(world, tmp_path) -> None:
    def counts():
        return [world.scalar(select(func.count()).select_from(t)) for t in
                (CourseSection, SectionRestriction)]
    before = counts()
    stats = _load(world, tmp_path, "soc_schedule_summer2026_sample.json", 2026, "7")
    assert counts() == before and stats.sections_inserted == 0


# ==========================================================================
# A. B. T. U. basic schedules, every meeting, several ranked options
# ==========================================================================


def test_a_t_u_lecture_courses_give_ranked_options(world) -> None:
    student = _student(world)
    result = _gen(world, student, ["01:830:101", "01:920:101"])
    assert result.status is ScheduleStatus.OPTIONS_FOUND
    assert len(result.options) == 10                                 # default cap
    _verify(result)
    assert result.metadata.search.nodes < result.metadata.search.cartesian_product


def test_b_every_meeting_of_a_section_is_checked(world) -> None:
    """01:750:203 = T/F lecture + one recitation; 01:750:193 = M/W lecture +
    recitation. Many 203 recitations clash with 193's MONDAY lecture - only a
    check of every meeting (not the first) catches them."""
    result = _gen(world, _student(world), ["01:750:193", "01:750:203"])
    assert result.status is ScheduleStatus.OPTIONS_FOUND
    _verify(result)
    for option in result.options:
        c203 = next(c for c in option.choices if c.course == "01:750:203")
        assert len(c203.meetings) >= 2


# ==========================================================================
# C. E. F. S. date-aware conflicts (real Summer 2026 sections)
# ==========================================================================


def test_c_s_same_time_same_session_is_impossible(world) -> None:
    """01:014:490 and 01:202:305: M/W 18:00-21:40, both Jul 6 - Aug 12. (202:305
    really requires 01:202:201 - the student has it, so eligibility passes.)"""
    student = _student(world, [*CS_RECORD, ("01:202:201", "20259")])
    result = _gen(world, student, ["01:014:490", "01:202:305"], term=SUMMER)
    assert result.status is ScheduleStatus.NO_VALID_SCHEDULE and not result.options
    issue = next(i for i in result.issues if i.code == "ALL_SECTIONS_CONFLICT")
    assert issue.courses == ["01:014:490", "01:202:305"]


def test_f_same_time_disjoint_sessions_do_not_conflict(world) -> None:
    """01:014:386 (Jul 6-31) and 01:202:201 (May 26 - Jul 2), both M/W 18:00-22:00."""
    result = _gen(world, _student(world), ["01:014:386", "01:202:201"], term=SUMMER)
    assert result.status is ScheduleStatus.OPTIONS_FOUND
    _verify(result)
    dates = {m.start_date for c in result.options[0].choices for m in c.meetings}
    assert len(dates) == 2


# ==========================================================================
# G. H. asynchronous, TBA / arranged / malformed
# ==========================================================================


def test_g_asynchronous_is_free_time(world) -> None:
    result = _gen(world, _student(world), ["01:013:143"])
    choice = result.options[0].choices[0]
    assert {m.kind for m in choice.meetings} == {MeetingKind.ASYNCHRONOUS}
    assert choice.time_verified and Reason.ALL_MEETING_TIMES_VERIFIED in result.options[0].reasons


def test_h_unknown_times_are_never_free_and_rank_last(world) -> None:
    tba = _gen(world, _student(world), ["01:013:321"])
    choice = tba.options[0].choices[0]
    assert any(m.kind is MeetingKind.TBA for m in choice.meetings) and not choice.time_verified
    assert any(i.code == "SCHEDULE_TIME_UNKNOWN" for i in tba.options[0].issues)
    arranged = _gen(world, _student(world), ["01:198:493"])
    assert all(m.kind is MeetingKind.ARRANGED for c in arranged.options[0].choices
               for m in c.meetings)
    # 07:966:333 publishes T/F 2330-1250 (real); its prerequisite keeps it out
    # of a request for this student, so read the candidates directly.
    from app.services.scheduling.candidates import StudentAttributes, load_term

    term = load_term(world, FALL, ["07:966:333"], StudentAttributes())
    bad = [c for c in term.candidates["07:966:333"]
           if any(m.kind is MeetingKind.MALFORMED for m in c.meetings)]
    assert bad and not any(c.time_verified for c in bad)
    assert bad[0].meetings[0].raw["start_time_military"] == "2330"
    # 01:750:203 has in-person sections and online-lecture (TBA LEC) ones:
    # every verified option ranks before any unverified one.
    mixed = _gen(world, _student(world), ["01:750:203"], max_results=25)
    verified = [o.choices[0].time_verified for o in mixed.options]
    assert verified == sorted(verified, reverse=True) and verified[0]


# ==========================================================================
# I. required companion (Physics lecture/recitation + lab record)
# ==========================================================================


def test_i_required_lab_record_is_scheduled_with_the_course(world) -> None:
    result = _gen(world, _student(world), ["01:750:193"])
    assert result.status is ScheduleStatus.OPTIONS_FOUND
    _verify(result)
    for option in result.options:
        assert sorted((c.component, c.supplement_code) for c in option.choices) == \
            [("primary", ""), ("required_companion", "LB")]
        assert Reason.REQUIRED_COMPONENTS_INCLUDED in option.reasons


# ==========================================================================
# J. K. L. section restrictions
# ==========================================================================


def test_j_restriction_satisfied_by_the_declared_major(world) -> None:
    """01:198:344 sections are open to major 198; the student is a CS major."""
    result = _gen(world, _student(world), ["01:198:344"])
    restricted = [c for o in result.options for c in o.choices if c.restriction.entries]
    assert restricted
    assert all(c.restriction.outcome is RestrictionOutcome.SATISFIED for c in restricted)
    assert restricted[0].restriction.matched.code == "198"


def test_l_restriction_for_another_major_is_unknown_not_eligible(world) -> None:
    """01:694:383 is open to "MAJ: 694"; the student is a CS major who may
    still hold a 694 major or minor CoursePilot has never recorded."""
    result = _gen(world, _student(world), ["01:694:383"])
    assert result.status is ScheduleStatus.OPTIONS_FOUND and result.options
    for option in result.options:
        for c in option.choices:
            assert c.restriction.outcome is RestrictionOutcome.UNKNOWN
        assert option.score.needs_confirmation == 1
        assert any(i.code == "SECTION_RESTRICTION_UNKNOWN" for i in option.issues)
        assert Reason.ALL_RESTRICTIONS_SATISFIED_OR_ABSENT not in option.reasons


def test_k_restriction_not_satisfied_needs_complete_attributes() -> None:
    """SYNTHETIC attributes: no CoursePilot record is complete today, so a
    definite failure is reachable only when attributes are declared complete."""
    from app.domain.schedule import RestrictionEntry
    from app.services.scheduling.candidates import (
        StudentAttributes,
        evaluate_restriction,
    )

    entries = [RestrictionEntry(kind="major", code="694")]
    partial = StudentAttributes(major_codes=frozenset({"198"}), unit_codes=frozenset({"01"}))
    complete = StudentAttributes(major_codes=frozenset({"198"}), unit_codes=frozenset({"01"}),
                                 complete=True)
    assert evaluate_restriction(entries, partial).outcome is RestrictionOutcome.UNKNOWN
    assert evaluate_restriction(entries, complete).outcome is RestrictionOutcome.NOT_SATISFIED
    minor = [RestrictionEntry(kind="minor", code="694")]
    assert evaluate_restriction(minor, complete).outcome is RestrictionOutcome.UNKNOWN
    unit = [RestrictionEntry(kind="unit", code="01")]
    assert evaluate_restriction(unit, partial).outcome is RestrictionOutcome.SATISFIED
    gated = evaluate_restriction(unit, partial, special_permission="Department staff")
    assert gated.outcome is RestrictionOutcome.UNKNOWN


def test_k_a_failed_restriction_is_a_hard_blocker(world, monkeypatch) -> None:
    from app.services.scheduling import candidates

    monkeypatch.setattr(candidates, "student_attributes", lambda s, st: candidates.StudentAttributes(
        major_codes=frozenset({"198"}), unit_codes=frozenset({"01"}), complete=True))
    from app.services.scheduling import service
    monkeypatch.setattr(service, "student_attributes", candidates.student_attributes)
    result = _gen(world, _student(world), ["01:694:383"])
    # Every 01:694:383 section is restricted to another major: none may appear.
    assert result.status is ScheduleStatus.NO_VALID_SCHEDULE and not result.options
    issue = next(i for i in result.issues if i.code == "SECTION_RESTRICTION_NOT_SATISFIED")
    assert issue.details["excluded_by"]["restriction_not_satisfied"] >= 1


# ==========================================================================
# M. N. O. P. cross-listing, instructors, availability
# ==========================================================================


def test_m_cross_listed_courses_are_never_the_same_class_twice(world) -> None:
    """01:013:120 index 10052 and 01:074:120 index 10053 are one class (each
    lists the other in crossListedSections): it is never registered twice."""
    result = _gen(world, _student(world), ["01:013:120", "01:074:120"])
    assert any(i.code == "REQUESTED_COURSES_CROSS_LISTED" for i in result.issues)
    assert result.status is ScheduleStatus.NO_VALID_SCHEDULE and not result.options
    alone = _gen(world, _student(world), ["01:013:120"])
    assert alone.options[0].choices[0].cross_listed_indexes == ["10053"]
    assert any(i.code == "CROSS_LISTED_SECTION" for i in alone.options[0].issues)
    _verify(result)
    pairs = {(a.index_number, b) for o in result.options for a in o.choices
             for b in a.cross_listed_indexes}
    chosen = [{c.index_number for c in o.choices} for o in result.options]
    assert not any(a in ch and b in ch for a, b in pairs for ch in chosen)


def test_n_multiple_instructors_are_kept_in_order(world) -> None:
    result = _gen(world, _student(world), ["01:070:105"])
    names = [c.instructors for o in result.options for c in o.choices]
    assert any(len(n) == 2 for n in names)


def test_o_p_availability_is_separate_and_archived(world) -> None:
    result = _gen(world, _student(world), ["01:750:193"], max_results=25)
    closed = [o for o in result.options if o.closed_sections_in_snapshot]
    assert closed, "a closed section can still prove a schedule exists"
    for o in closed:
        assert Reason.ALL_SECTIONS_OPEN_IN_SNAPSHOT not in o.reasons
        assert o.score.closed_sections_if_live == 0      # archived: not a ranking signal
    for o in result.options:
        for c in o.choices:
            section = world.scalar(select(CourseSection).where(
                CourseSection.index_number == c.index_number, CourseSection.term_code == FALL))
            assert (c.availability.state is AvailabilityState.OPEN) == section.open_status
            assert c.availability.observed_at
    assert result.metadata.availability_freshness is AvailabilityFreshness.ARCHIVED
    opened = _gen(world, _student(world), ["01:920:101"])
    assert any(Reason.ALL_SECTIONS_OPEN_IN_SNAPSHOT in o.reasons for o in opened.options)


def test_weekend_meetings_are_scheduled(world) -> None:
    result = _gen(world, _student(world), ["01:090:182"])
    assert "S" in {m.day for o in result.options for c in o.choices for m in c.meetings}


# ==========================================================================
# Q. R. user constraints; relaxation is reported, never applied
# ==========================================================================


def test_q_earliest_start_is_hard(world) -> None:
    result = _gen(world, _student(world), ["01:830:101", "01:920:101"],
                  preferences=SchedulePreferences(earliest_start="12:00"))
    _verify(result)
    assert result.options
    for o in result.options:
        for c in o.choices:
            assert all(m.start_minute >= 720 for m in c.meetings if m.kind is MeetingKind.TIMED)


def test_r_minimum_break_is_applied_between_classes(world) -> None:
    prefs = SchedulePreferences(minimum_minutes_between_classes=60)
    result = _gen(world, _student(world), ["01:830:101", "01:920:101", "01:070:101"],
                  preferences=prefs)
    assert result.options
    _verify(result, buffer=60)


def test_impossible_constraints_are_named_not_relaxed(world) -> None:
    prefs = SchedulePreferences(earliest_start="22:00", avoid_days=["F"])
    result = _gen(world, _student(world), ["01:830:101"], preferences=prefs)
    assert result.status is ScheduleStatus.NO_VALID_SCHEDULE and not result.options
    too_strict = next(i for i in result.issues if i.code == "USER_CONSTRAINTS_TOO_STRICT")
    assert too_strict.details["individually_blocking"] == ["earliest_start"]
    assert result.preferences == prefs                  # echoed unchanged


# ==========================================================================
# V. W. X. Y. Z. determinism, read-only, eligibility, unpublished, duplicates
# ==========================================================================


def test_u_v_determinism_and_input_order(world) -> None:
    student = _student(world)
    courses = ["01:920:101", "01:830:101", "01:070:101"]
    first = _gen(world, student, courses).canonical_json()
    for seed in range(3):
        shuffled = list(courses)
        random.Random(seed).shuffle(shuffled)
        world.expire_all()
        assert _gen(world, student, shuffled).canonical_json() == first


def _hashes(session):
    out = {}
    for table in Base.metadata.sorted_tables:
        rows = session.execute(table.select()).all()
        out[table.name] = hashlib.sha256(repr(sorted(map(repr, rows))).encode()).hexdigest()
    return out


def test_w_generation_is_read_only(world) -> None:
    student = _student(world)
    before = _hashes(world)
    _gen(world, student, ["01:750:193", "01:830:101"])
    _gen(world, student, ["01:014:490", "01:014:386"], term=SUMMER)
    assert not (world.new or world.dirty or world.deleted)
    world.commit()
    assert _hashes(world) == before


def test_x_course_eligibility_is_always_rechecked(world) -> None:
    """01:198:211 needs 01:198:112 with C (real courseNotes); the student has
    only 111. The scheduler does not decide this - check_proposal does."""
    student = _student(world, [("01:198:111", "20259")])
    result = _gen(world, student, ["01:198:211"])
    assert result.status is ScheduleStatus.COURSE_ELIGIBILITY_FAILED and not result.options
    assert any(i.code == "COURSE_NOT_ELIGIBLE" for i in result.issues)


def test_y_unpublished_term_is_never_filled_from_another_term(world) -> None:
    result = _gen(world, _student(world), ["01:830:101"], term="20279")
    assert result.status is ScheduleStatus.TERM_SCHEDULE_NOT_PUBLISHED and not result.options


def test_z_duplicate_requests_are_scheduled_once(world) -> None:
    result = _gen(world, _student(world), ["01:830:101", "01:830:101", "01:920:101"])
    assert result.requested_courses == ["01:830:101", "01:920:101"]
    assert any(i.code == "DUPLICATE_COURSE_REQUEST" for i in result.issues)
    assert result.options and all(len(o.choices) == 2 for o in result.options)
    _verify(result)


def test_unknown_course_and_bad_input(world) -> None:
    result = _gen(world, _student(world), ["01:830:101", "01:999:999"])
    assert result.status is ScheduleStatus.NO_VALID_SCHEDULE
    assert any(i.code == "COURSE_NOT_OFFERED" and i.courses == ["01:999:999"]
               for i in result.issues)
    with pytest.raises(InvalidScheduleRequest):
        _gen(world, _student(world), ["CS 344"])
    with pytest.raises(InvalidScheduleRequest):
        _gen(world, _student(world), ["01:830:101"], max_results=500)
    with pytest.raises(InvalidScheduleRequest):
        _gen(world, _student(world), [f"01:830:{n}" for n in range(101, 111)])


def test_wrong_term_sections_are_never_mixed(world) -> None:
    """01:830:101 has Fall sections only: asked for Summer, it is not offered -
    Fall sections are never borrowed."""
    result = _gen(world, _student(world), ["01:830:101", "01:014:386"], term=SUMMER)
    assert any(i.code == "COURSE_NOT_OFFERED" and i.courses == ["01:830:101"]
               for i in result.issues)
    assert not result.options


def test_option_json_is_explainable(world) -> None:
    result = _gen(world, _student(world), ["01:198:344"])
    payload = json.loads(result.canonical_json())
    choice = payload["options"][0]["choices"][0]
    for field in ("course", "section_number", "index_number", "campus_code", "meetings",
                  "instructors", "availability", "restriction", "cross_listed_indexes"):
        assert field in choice
    assert payload["metadata"]["ranking_objective"][0] == "needs_confirmation"
    assert payload["metadata"]["section_dataset"]
