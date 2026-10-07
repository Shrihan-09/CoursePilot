"""Labs, recitations, workshops and linked components (Phase 6.6.1) - real SOC data.

Three relationships, proven distinct on real Rutgers records
(docs/investigations/phase-6-6-1-linked-component-semantics.md):

    MEETING       one index, several meetings     01:750:203 (LEC+RECIT), 01:119:115 (LEC+WORKSHOP)
    REGISTRATION  one course, two records/indexes 01:750:193/194 + "LB" (0 credits), 01:750:202 + "LB"
    ACADEMIC      course X requires course Y      01:750:205 -> 01:750:203, 01:750:229 -> 01:750:227
                                                  (on 26 of 27 sections), 01:119:117 -> 01:119:116

Fixtures are whole, unmodified records copied by
scripts/make_component_fixture.py and loaded by the production course AND
section pipelines (so co-requisites come from the real loader). A copy edited
for an edge case the archive lacks is labelled SYNTHETIC.
"""

from __future__ import annotations

import copy
import json
import uuid
from decimal import Decimal

import pytest
from app.domain.prerequisites import PrereqStatus
from app.domain.schedule import MeetingKind, Reason, RestrictionOutcome, ScheduleStatus
from app.models import (
    Course,
    CourseCorequisite,
    ProgramVersion,
    Student,
    StudentCourse,
)
from app.services.course_eligibility import check_proposal
from app.services.scheduling.service import generate_schedule
from sqlalchemy import select

from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import CS_REQUIREMENTS, FIXTURE_DIR

FALL26, FALL25, SUMMER26 = "20269", "20259", "20267"
FILES = {(2026, "9"): "soc_components_fall2026_sample.json",
         (2025, "9"): "soc_components_fall2025_sample.json",
         (2026, "7"): "soc_components_summer2026_sample.json"}


def _records(year, term):
    return json.loads((FIXTURE_DIR / FILES[(year, term)]).read_bytes())


def _load(session, tmp_path, year, term, records=None):
    query = SocQuery(year=year, term=term, campus="NB")
    folder = tmp_path / f"{year}{term}"
    folder.mkdir(exist_ok=True)
    payload = json.dumps(records if records is not None else _records(year, term))
    (folder / f"soc_courses_{year}_{term}_NB.json").write_text(payload, encoding="utf-8")
    CourseIngestionPipeline(session, folder).run(query, coverage="complete")
    stats = SectionIngestionPipeline(session, folder).run(query)
    assert stats.unmatched_offering == 0 and stats.validation_failed == 0


def _world(session, tmp_path, fall26=None):
    _load(session, tmp_path, 2026, "9", fall26)
    _load(session, tmp_path, 2025, "9")
    _load(session, tmp_path, 2026, "7")
    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    session.commit()
    return session


@pytest.fixture
def world(session, tmp_path):
    return _world(session, tmp_path)


def _student(session, records=()):
    version = session.scalar(select(ProgramVersion))
    student = Student(external_ref=f"lc-{uuid.uuid4().hex[:8]}",
                      catalog_year=version.catalog_year, program_version_id=version.id)
    session.add(student)
    session.flush()
    for key, term in records:
        course = session.scalar(select(Course).where(Course.course_string == key,
                                                     Course.supplement_code == ""))
        assert course is not None, key
        session.add(StudentCourse(student_id=student.id, course_id=course.id, term_code=term,
                                  status="completed", grade="A", credits_earned=course.credits))
    session.commit()
    return student


PHYSICS = [("01:750:193", "20259"), ("01:750:271", "20259"), ("01:640:151", "20259"),
           ("01:640:152", "20259"), ("01:119:115", "20259"), ("01:160:307", "20259"),
           ("01:160:313", "20261")]


def _gen(session, student, courses, term=FALL26, **kw):
    return generate_schedule(session, student, term_code=term, courses=courses, **kw)


def _clash(a, b):
    """Independent oracle: two timed meetings overlap (dates unknown = overlap)."""
    if a.kind is not MeetingKind.TIMED or b.kind is not MeetingKind.TIMED or a.day != b.day:
        return False
    if None not in (a.start_date, a.end_date, b.start_date, b.end_date) and (
            a.end_date < b.start_date or b.end_date < a.start_date):
        return False
    return max(a.start_minute, b.start_minute) < min(a.end_minute, b.end_minute)


def _no_clash(result):
    for o in result.options:
        for i, a in enumerate(o.choices):
            for b in o.choices[i + 1:]:
                assert not any(_clash(x, y) for x in a.meetings for y in b.meetings), \
                    (a.index_number, b.index_number)


# ==========================================================================
# 1. MEETING relationship: one index, several meetings - never two registrations
# ==========================================================================


def test_one_index_with_several_meetings_is_one_registration(world) -> None:
    student = _student(world, PHYSICS)
    for course, kinds in (("01:750:203", ["LEC", "RECIT"]), ("01:119:116", ["LEC", "WORKSHOP"])):
        result = _gen(world, student, [course])
        assert result.status is ScheduleStatus.OPTIONS_FOUND, course
        for o in result.options:
            [choice] = o.choices                               # ONE registration
            assert choice.component == "primary" and choice.supplement_code == ""
            assert choice.meeting_components == kinds
        assert not [r for r in result.relationships if r.course == course]


# ==========================================================================
# 2. REGISTRATION relationship: a required second record of the same course
# ==========================================================================


@pytest.mark.parametrize("course", ["01:750:193", "01:750:194"])
def test_required_lab_record_is_always_in_the_bundle(world, course) -> None:
    """01:750:194 says "MUST REGISTER BOTH LEC/REC & LAB" - wording Phase 6.6
    missed (it scheduled 194 WITHOUT its lab)."""
    result = _gen(world, _student(world, PHYSICS), [course], max_results=25)
    assert result.status is ScheduleStatus.OPTIONS_FOUND and result.options
    _no_clash(result)
    for o in result.options:
        parts = sorted((c.component, c.supplement_code, c.credits) for c in o.choices)
        assert parts == [("primary", "", Decimal(4)),
                         ("required_companion", "LB", Decimal(0))]
        lab = next(c for c in o.choices if c.component == "required_companion")
        assert lab.meeting_components == ["LAB"]
        assert "REGISTER" in lab.component_evidence and "BOTH" in lab.component_evidence
        assert o.total_credits == Decimal(4)              # the 0-credit lab adds nothing
        assert Reason.REQUIRED_COMPONENTS_INCLUDED in o.reasons
    [link] = [r for r in result.relationships if r.kind == "registration_component"]
    assert (link.course, link.related) == (course, f"{course} LB")
    assert link.evidence and link.source.startswith("sectionNotes index")
    assert not any(i.code == "LINKED_COMPONENT_UNVERIFIED" for i in result.issues)


def test_extended_general_physics_lab_record_is_linked(world) -> None:
    """01:750:202 (Fall 2025): 5-credit base + 0-credit LB; "MUST REGISTER FOR
    BOTH A REC AND A LAB SECTION" on LB index 11791 and on base index 11793."""
    result = _gen(world, _student(world, PHYSICS), ["01:750:202"], term=FALL25)
    assert result.options
    for o in result.options:
        assert {c.component for c in o.choices} == {"primary", "required_companion"}


def test_an_unproven_second_record_is_never_bundled_nor_silently_dropped(session,
                                                                       tmp_path) -> None:
    """SYNTHETIC: the real 01:750:193 records with every "both" note removed.
    The 0-credit LB record alone is NOT proof of a requirement."""
    records = copy.deepcopy(_records(2026, "9"))
    for c in records:
        if c["courseString"] == "01:750:193":
            for s in c["sections"]:
                s["sectionNotes"] = ""
    world = _world(session, tmp_path, records)
    result = _gen(world, _student(world, PHYSICS), ["01:750:193"])
    assert all(len(o.choices) == 1 for o in result.options)
    assert any(i.code == "LINKED_COMPONENT_UNVERIFIED" for i in result.issues)
    assert [r.kind for r in result.relationships] == ["registration_component_unverified"]


def test_a_conflicting_required_lab_is_a_blocker_never_an_omission(session, tmp_path) -> None:
    """SYNTHETIC times: every real LB lab of 01:750:193 moved onto the lecture
    hour (M 14:15-15:10). The lecture is never scheduled without its lab."""
    records = copy.deepcopy(_records(2026, "9"))
    for c in records:
        if c["courseString"] == "01:750:193" and c["supplementCode"].strip() == "LB":
            for s in c["sections"]:
                for m in s["meetingTimes"]:
                    m.update(meetingDay="M", startTimeMilitary="1415", endTimeMilitary="1510")
    world = _world(session, tmp_path, records)
    result = _gen(world, _student(world, PHYSICS), ["01:750:193"])
    assert result.status is ScheduleStatus.NO_VALID_SCHEDULE and not result.options
    issue = next(i for i in result.issues if i.code == "ALL_SECTIONS_CONFLICT")
    assert issue.details["slots"] == ["01:750:193", "01:750:193#LB"]


def test_a_lab_restriction_is_evaluated_on_the_lab(session, tmp_path) -> None:
    """SYNTHETIC restriction: the real LB sections restricted to "MAJ: 999"
    (no LB section in the archive has its own restriction)."""
    records = copy.deepcopy(_records(2026, "9"))
    for c in records:
        if c["courseString"] == "01:750:193" and c["supplementCode"].strip() == "LB":
            for s in c["sections"]:
                s["majors"] = [{"code": "999", "isMajorCode": True, "isUnitCode": False}]
    world = _world(session, tmp_path, records)
    result = _gen(world, _student(world, PHYSICS), ["01:750:193"])
    for o in result.options:
        lab = next(c for c in o.choices if c.component == "required_companion")
        assert lab.restriction.outcome is RestrictionOutcome.UNKNOWN
        assert any(i.code == "SECTION_RESTRICTION_UNKNOWN" and lab.index_number in i.indexes
                   for i in o.issues)
        assert o.score.needs_confirmation >= 1


# ==========================================================================
# 3. ACADEMIC relationship: a separate course, Phase 6.4's verdict
# ==========================================================================


def test_academic_corequisite_course_is_required_and_counted(world) -> None:
    """01:750:205 GENERAL PHYSICS LAB (1 credit): "01:750:203 IS A CO-REQUISITE"."""
    student = _student(world, PHYSICS)
    alone = _gen(world, student, ["01:750:205"])
    assert alone.status is ScheduleStatus.COURSE_ELIGIBILITY_FAILED and not alone.options
    blocker = next(i for i in alone.issues if i.code == "COURSE_NOT_ELIGIBLE")
    assert blocker.details["missing_corequisites"] == ["01:750:203"]
    both = _gen(world, student, ["01:750:203", "01:750:205"])
    assert both.options
    _no_clash(both)
    assert [(r.kind, r.course, r.related) for r in both.relationships] == \
        [("academic_corequisite", "01:750:205", "01:750:203")]
    for o in both.options:
        assert {c.component for c in o.choices} == {"primary"}   # two COURSES, not a bundle
        assert o.total_credits == Decimal(4)                   # 3 + the 1-credit lab


def test_corequisite_on_some_sections_is_never_satisfied_silently(world) -> None:
    """01:750:229: "01:750:227 IS A CO-REQUSITE" (Rutgers' spelling) on 26 of 27
    sections. Before 6.6.1 nothing was stored and eligibility said SATISFIED."""
    row = world.scalar(select(CourseCorequisite).join(Course, Course.id == CourseCorequisite.course_id)
                       .where(Course.course_string == "01:750:229",
                              CourseCorequisite.term_code == FALL26))
    assert (row.source_field, row.classification) == ("sectionNotes:some", "parsed")
    assert "26 of 27 sections" in row.parse_detail and "CO-REQUSITE" in row.raw_text
    student = _student(world, PHYSICS)
    alone = check_proposal(world, student, ["01:750:229"], FALL26)["01:750:229"]
    assert alone.status is PrereqStatus.UNKNOWN
    assert "corequisite_on_some_sections" in alone.corequisite.reasons
    paired = check_proposal(world, student, ["01:750:229"], FALL26, {"01:750:227"},
                            independent=True)["01:750:229"]
    assert paired.status is PrereqStatus.SATISFIED
    result = _gen(world, student, ["01:750:227", "01:750:229"])
    assert result.options and ("academic_corequisite", "01:750:229", "01:750:227") in \
        [(r.kind, r.course, r.related) for r in result.relationships]


def test_bio_research_lab_corequisite_on_two_sections(world) -> None:
    """01:119:117: "PRE-REQ: 119:115 CO-REQ: 119:116" on 2 of 27 Fall 2026 sections."""
    student = _student(world, PHYSICS)
    alone = check_proposal(world, student, ["01:119:117"], FALL26)["01:119:117"]
    assert alone.status is PrereqStatus.UNKNOWN
    with_lecture = check_proposal(world, student, ["01:119:117"], FALL26, {"01:119:116"},
                                  independent=True)["01:119:117"]
    assert with_lecture.status is PrereqStatus.SATISFIED


def test_a_separate_lab_course_without_a_corequisite_stands_alone(world) -> None:
    """01:160:171 INTR EXPERIMENTATION publishes a prerequisite only: it is a
    1-credit course of its own - no bundle, no invented co-requisite."""
    result = _gen(world, _student(world, [("01:160:161", "20259")]), ["01:160:171"])
    assert result.options and not result.relationships
    for o in result.options:
        [c] = o.choices
        assert c.meeting_components == ["LAB"] and c.credits == Decimal(1)


def test_cross_course_registration_prose_is_read_not_dropped(world) -> None:
    """01:617:201 (Fall 2025): "STUDENTS MU ST ALSO REGISTER FOR 01:078:117" and
    "MUST ALSO REG ISTER FOR 01:013:252 OR 01:563:1 31" (wrap damage is Rutgers')."""
    row = world.scalar(select(CourseCorequisite).join(Course, Course.id == CourseCorequisite.course_id)
                       .where(Course.course_string == "01:617:201"))
    assert row is not None and row.source_field == "sectionNotes:some"
    check = check_proposal(world, _student(world), ["01:617:201"], FALL25)["01:617:201"]
    assert check.status is not PrereqStatus.SATISFIED


# ==========================================================================
# 4. meetings Rutgers describes only in prose; prose pairings; session dates
# ==========================================================================


def test_meeting_times_in_notes_are_never_verified(world) -> None:
    """Summer 2026 01:160:308 index 00557: structured meetings are LEC only;
    the note says "RECIT: TWH 8:00-8:50AM"."""
    result = _gen(world, _student(world, PHYSICS), ["01:160:308"], term=SUMMER26,
                  max_results=25)
    chosen = [c for o in result.options for c in o.choices]
    assert chosen and all(not c.time_verified for c in chosen)
    c557 = next(c for c in chosen if c.index_number == "00557")
    assert "RECIT: TWH 8:00-8:50AM" in c557.meeting_text_in_notes
    assert all(any(i.code == "MEETING_TIME_IN_NOTES" for i in o.issues) for o in result.options)
    assert all(Reason.ALL_MEETING_TIMES_VERIFIED not in o.reasons for o in result.options)
    # 01:119:115 Summer: structured W 09:30-10:50 WORKSHOP, prose "MW 9:30AM-10:50"
    bio = _gen(world, _student(world), ["01:119:115"], term=SUMMER26)
    assert all(not c.time_verified for o in bio.options for c in o.choices)


def test_a_prose_section_pairing_with_another_requested_course_is_flagged(world) -> None:
    """00557: "STUDENTS ALSO REGISTERED FOR LAB SECTION 01:160:314:H1 OR H2 MUST
    TAKE THIS SECTION OF LECTURE" - no machine-readable pairing exists."""
    result = _gen(world, _student(world, PHYSICS), ["01:160:308", "01:160:314"],
                  term=SUMMER26, max_results=25)
    with_557 = [o for o in result.options
                if any(c.index_number == "00557" for c in o.choices)]
    assert with_557
    for o in with_557:
        issue = next(i for i in o.issues
                     if i.code == "SECTION_NOTE_REFERENCES_REQUESTED_COURSE")
        assert issue.courses == ["01:160:308", "01:160:314"]
    _no_clash(result)


def test_components_in_different_sessions_use_their_own_dates(world) -> None:
    """01:160:308 Summer: index 00555 runs May 26 - Jul 2, 00557/00558 Jul 6 - Aug 12."""
    result = _gen(world, _student(world, PHYSICS), ["01:160:308"], term=SUMMER26,
                  max_results=25)
    dates = {(c.index_number, m.start_date, m.end_date)
             for o in result.options for c in o.choices for m in c.meetings}
    assert {(i, str(s)) for i, s, _ in dates} >= {("00555", "2026-05-26"),
                                                    ("00557", "2026-07-06")}


# ==========================================================================
# 5. the Planning Engine plans COURSES; the scheduler expands registrations
# ==========================================================================


def test_planner_plans_courses_and_the_scheduler_adds_the_lab(world) -> None:
    """SYNTHETIC requirements (two course requirements, real courses): the
    Planning Engine must plan 01:750:194 once (no LB course) and 01:750:229
    WITH its academic co-requisite 01:750:227; the Schedule Engine then
    expands 194 into lecture + LB lab."""
    from app.services.planning.service import generate_plan

    definition = json.loads(CS_REQUIREMENTS.read_bytes())
    definition["program"]["variant"] = "physics-test"
    definition["requirements"] = [
        {"code": "PHYS_ROOT", "name": "Physics (test)", "requirement_type": "all_of",
         "sort_order": 0, "source_prose": "SYNTHETIC test requirement"},
        {"code": "PHYS_194", "name": "Physics for Sciences II", "requirement_type": "course",
         "parent": "PHYS_ROOT", "sort_order": 0, "courses": ["01:750:194"],
         "source_prose": "SYNTHETIC test requirement"},
        {"code": "PHYS_LAB", "name": "Analytical Physics II Lab", "requirement_type": "course",
         "parent": "PHYS_ROOT", "sort_order": 1, "courses": ["01:750:229"],
         "source_prose": "SYNTHETIC test requirement"},
    ]
    definition["program_rules"] = []
    RequirementLoader(world).load(definition, json.dumps(definition).encode())
    world.commit()
    student = _student(world, PHYSICS)
    plan = generate_plan(world, student, start_term=FALL26,
                         program_key_="sas-198-ba-physics-test")
    placed = {pc.course: pc for t in plan.terms for pc in t.courses}
    assert "01:750:194" in placed and not any(k.endswith("LB") for k in placed)
    assert placed["01:750:194"].credits == Decimal(4)
    assert placed["01:750:229"].term_code == placed["01:750:227"].term_code
    assert "01:750:227" in placed["01:750:229"].corequisite_group
    term = [pc.course for pc in plan.terms[0].courses]
    result = _gen(world, student, term)
    assert result.options
    _no_clash(result)
    kinds = sorted({r.kind for r in result.relationships})
    assert kinds == ["academic_corequisite", "registration_component"]
    for o in result.options:
        assert sorted(c.course + "/" + c.component for c in o.choices) == sorted(
            [*(f"{k}/primary" for k in term), "01:750:194/required_companion"])


def test_bundles_are_deterministic_and_input_order_free(world) -> None:
    student = _student(world, PHYSICS)
    a = _gen(world, student, ["01:750:194", "01:750:203", "01:750:205"]).canonical_json()
    world.expire_all()
    b = _gen(world, student, ["01:750:205", "01:750:194", "01:750:203"]).canonical_json()
    assert a == b
