"""Course eligibility through the REAL ingestion pipeline (Phase 6.4).

Real archived CS SOC records. 01:198:112's real `courseNotes` is
"A grade below a 'C' in a prerequisite course will not satisfy prereq".
Two co-requisites are ADDED to copies of real records (labelled synthetic
below); their wording is verbatim Rutgers SOC wording from other courses.
"""

from __future__ import annotations

import json

import pytest
from app.domain.prerequisites import PrereqStatus
from app.models import (
    Course,
    CourseCorequisite,
    CoursePrerequisite,
    ProgramVersion,
    Student,
    StudentCourse,
)
from app.services.course_eligibility import check_proposal
from app.services.prerequisites import check
from sqlalchemy import func, select

from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import CS_REQUIREMENTS, FIXTURE_DIR

S, U, K = PrereqStatus.SATISFIED, PrereqStatus.UNSATISFIED, PrereqStatus.UNKNOWN
FALL_26 = SocQuery(year=2026, term="9", campus="NB")
TERM = "20269"


def _records():
    records = json.loads((FIXTURE_DIR / "soc_cs_courses_sample.json").read_bytes())
    by_key = {r["courseString"]: r for r in records}
    # SYNTHETIC: "PRE OR COREQ" on 112 naming its own prerequisite (wording as
    # 01:830:341's "PREREQ OR COREQ: 830:340").
    by_key["01:198:112"]["courseNotes"] += " PREREQ OR COREQ: 01:198:111"
    # SYNTHETIC: a section-level co-requisite on EVERY section of 211 (wording
    # as 14:332:221's "CO-REQ: 14:332:223").
    for section in by_key["01:198:211"]["sections"]:
        section["sectionNotes"] = "CO-REQ: 01:198:205"
    return records


@pytest.fixture
def world(session, tmp_path):
    path = tmp_path / f"soc_courses_{FALL_26.year}_{FALL_26.term}_{FALL_26.campus}.json"
    path.write_text(json.dumps(_records()), encoding="utf-8")
    stats = CourseIngestionPipeline(session, tmp_path).run(FALL_26)
    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    session.commit()
    return session, stats, tmp_path


def _student(session, records):
    version = session.scalar(select(ProgramVersion))
    student = Student(external_ref="e", catalog_year=version.catalog_year,
                      program_version_id=version.id)
    session.add(student)
    session.flush()
    for key, term, status, grade in records:
        course = session.scalar(select(Course).where(Course.course_string == key))
        session.add(StudentCourse(student_id=student.id, course_id=course.id, term_code=term,
                                  status=status, grade=grade, credits_earned=course.credits))
    session.commit()
    return student


def test_conditions_are_interpreted_and_raw_text_kept(world) -> None:
    session, _, _ = world
    row = session.scalar(select(CoursePrerequisite).join(Course).where(
        Course.course_string == "01:198:211"))
    assert row.condition_note == "A grade below a 'C' in a prerequisite course will not satisfy prereq"
    assert row.interpreted_conditions["minimum_grade"] == {
        "grade": "C", "scope": "all", "courses": [], "text": row.condition_note}
    assert row.interpreted_conditions["uninterpreted"] == []
    assert row.raw_text.startswith("(01:198:112 DATA STRUCTURES )")         # verbatim SOC


@pytest.mark.parametrize(("grade", "expected"), [("B", S), ("C", S), ("D", U)])
def test_prerequisite_minimum_grade(world, grade, expected) -> None:
    session, _, _ = world
    student = _student(session, [("01:198:112", "20261", "completed", grade)])
    result = check(session, student, "01:198:211", TERM)
    assert result.status is expected
    [g] = result.evidence.grade_checks
    assert (g["course"], g["required_grade"], g["earned_grade"]) == ("01:198:112", "C", grade)


def test_prerequisite_in_progress_is_unknown(world) -> None:
    session, _, _ = world
    student = _student(session, [("01:198:112", "20269", "in_progress", None)])
    assert check(session, student, "01:198:211", TERM).status is K


def test_section_level_corequisite_is_lifted_only_when_uniform(world) -> None:
    session, _, _ = world
    coreq = session.scalar(select(CourseCorequisite).join(Course).where(
        Course.course_string == "01:198:211"))
    assert coreq.source_field == "sectionNotes:all"
    assert coreq.raw_text == "CO-REQ: 01:198:205"
    assert coreq.canonical_text == "CONCURRENT[01:198:205: same term; earlier unspecified]"


def test_corequisite_needs_the_same_term(world) -> None:
    session, _, _ = world
    student = _student(session, [("01:198:112", "20261", "completed", "B")])
    alone = check_proposal(session, student, ["01:198:211"], TERM)["01:198:211"]
    assert (alone.status, alone.combination) == (U, "prerequisite AND corequisite")
    together = check_proposal(session, student, ["01:198:211"], TERM, {"01:198:205"})
    assert together["01:198:211"].status is S


def test_corequisite_completed_earlier_is_unknown_for_coreq_only_wording(world) -> None:
    session, _, _ = world
    student = _student(session, [("01:198:112", "20261", "completed", "B"),
                                 ("01:198:205", "20261", "completed", "A")])
    result = check_proposal(session, student, ["01:198:211"], TERM)["01:198:211"]
    assert result.status is K
    assert "corequisite_prior_completion_unspecified:01:198:205" in result.corequisite.reasons


def test_pre_or_coreq_relaxes_the_prerequisite(world) -> None:
    session, _, _ = world
    nothing = _student(session, [])
    assert check_proposal(session, nothing, ["01:198:112"], TERM)["01:198:112"].status is U
    same_term = check_proposal(session, nothing, ["01:198:112"], TERM, {"01:198:111"})["01:198:112"]
    assert (same_term.status, same_term.combination) == (S, "prerequisite OR corequisite")


def test_a_low_grade_earlier_does_not_satisfy_the_relaxed_path(world) -> None:
    """The 'C or better' note governs 111 whether it is the prerequisite or
    the earlier completion of a PRE-OR-COREQ: a D satisfies neither."""
    session, _, _ = world
    student = _student(session, [("01:198:111", "20261", "completed", "D")])
    result = check_proposal(session, student, ["01:198:112"], TERM)["01:198:112"]
    assert result.status is U


def test_corequisites_are_term_scoped(world) -> None:
    session, _, _ = world
    student = _student(session, [("01:198:112", "20261", "completed", "B")])
    other = check_proposal(session, student, ["01:198:211"], "20271", {"01:198:205"})
    assert other["01:198:211"].prerequisite.reasons == ["no_offering_in_term"]
    assert other["01:198:211"].corequisite.has_corequisite is False


def test_reloading_is_idempotent(world) -> None:
    session, first, tmp_path = world
    before = (session.scalar(select(func.count()).select_from(CourseCorequisite)),
              session.scalar(select(func.count()).select_from(CoursePrerequisite)))
    again = CourseIngestionPipeline(session, tmp_path).run(FALL_26)
    after = (session.scalar(select(func.count()).select_from(CourseCorequisite)),
             session.scalar(select(func.count()).select_from(CoursePrerequisite)))
    assert before == after
    assert again.prerequisites.get("corequisites_inserted", 0) == 0
    assert again.prerequisites.get("corequisites_unchanged") == before[0]
