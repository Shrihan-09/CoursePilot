"""Phase 7.3: math placement conditions and section scheduling constraints.

The Math world of the planning tests (real 01:640 sample records, real
curated Math Option A) plus two SYNTHETIC records copied from 01:640:151 and
changed to imitate wording Rutgers really publishes:

    01:640:026  no prerequisite; its section notes "STUDENTS WANTING THIS
                SECTION; MUST REGISTER FOR 640:001:20" (01:640:025, Spring)
    01:640:115  prerequisite "(01:640:026 INTERMEDIATE ALGEBRA )"; every
                section notes "PREREQ: 640:026 OR PLACEMENT" (Spring 2021,
                2024-2027)

Fabricated students only; no network.
"""

from __future__ import annotations

import copy

import pytest
from app.domain.attempts import Attempt
from app.domain.planning import EligibilityState, Reason
from app.domain.prerequisites import PrereqStatus
from app.models import Course, CourseCorequisite
from app.services.course_eligibility import check_proposal
from app.services.placement import MET_BY_PLACEMENT, REQUIRES_PLACEMENT
from app.services.prerequisites import check_many
from sqlalchemy import func, select

from coursepilot_ingestion.loaders.requirements import RequirementLoader

from .conftest import MATH_REQUIREMENTS
from .test_planning_engine import _placed, _plan, _records, _student, _terms

RULES = "20269"


def _clone(base, number, prereq, note, index):
    record = copy.deepcopy(base)
    record.update(courseString=f"01:640:{number}", courseNumber=number,
                  title=f"SYNTHETIC {number}", expandedTitle="", preReqNotes=prereq,
                  courseNotes="")
    section = copy.deepcopy(base["sections"][0])
    section.update(index=index, number="01", sectionNotes=note, crossListedSections=[])
    record["sections"] = [section]
    return record


@pytest.fixture
def placement_world(session, tmp_path):
    records = _records("soc_math_courses_sample.json")
    calc = next(r for r in records if r["courseString"] == "01:640:151")
    records += [
        _clone(calc, "026", "",
               "STUDENTS WANTING THIS SECTION; MUST REGISTER FOR 640:001:20", "97001"),
        _clone(calc, "115", "(01:640:026 INTERMEDIATE ALGEBRA )",
               "PREREQ: 640:026 OR PLACEMENT", "97002"),
    ]
    _terms(session, tmp_path, records)
    RequirementLoader(session).load_file(MATH_REQUIREMENTS)
    session.commit()
    return session


def _fresh(session, placement=None):
    student = _student(session, [], program_code="640")
    student.math_placement = placement
    session.commit()
    return student


def _check(session, student, key, *, passed=()):
    projected = [Attempt(k, "20259", "completed", "A") for k in passed]
    return check_many(session, student, [key], RULES, projected=projected)[key]


def test_a_section_registration_note_is_not_a_corequisite(placement_world):
    s = placement_world
    rows = s.scalar(select(func.count()).select_from(CourseCorequisite)
                    .join(Course, Course.id == CourseCorequisite.course_id)
                    .where(Course.course_string == "01:640:026"))
    assert rows == 0
    student = _fresh(s)
    check = check_proposal(s, student, ["01:640:026"], RULES)["01:640:026"]
    assert check.status is PrereqStatus.SATISFIED
    assert not check.corequisite.has_corequisite


def test_without_a_placement_the_published_prerequisite_decides(placement_world):
    s = placement_world
    student = _fresh(s)
    # 026 completed (or planned): "026 OR PLACEMENT" is met - nothing flagged.
    met = _check(s, student, "01:640:115", passed=["01:640:026"])
    assert met.status is PrereqStatus.SATISFIED and REQUIRES_PLACEMENT not in met.reasons
    # 026 not taken: placement COULD meet it, so unknown - never assumed met.
    unmet = _check(s, student, "01:640:115")
    assert unmet.status is PrereqStatus.UNKNOWN and unmet.reasons == [REQUIRES_PLACEMENT]


def test_without_a_placement_the_published_path_is_planned_unflagged(placement_world):
    s = placement_world
    plan = _plan(s, _fresh(s))
    placed = _placed(plan)
    assert {"01:640:026", "01:640:115", "01:640:151"} <= set(placed)
    pre = placed["01:640:115"]
    assert pre.prerequisite.state is EligibilityState.CONDITIONAL_ON_PLAN
    assert REQUIRES_PLACEMENT not in pre.prerequisite.unknown_reasons
    assert Reason.REQUIRES_MATH_PLACEMENT not in pre.reasons
    assert placed["01:640:026"].term_code < pre.term_code < placed["01:640:151"].term_code
    assert plan.placement is None


def test_placed_into_calculus_needs_nothing_below_it(placement_world):
    s = placement_world
    student = _fresh(s, "01:640:151")
    for key in ("01:640:151", "01:640:115"):          # 151 itself, and below it
        met = _check(s, student, key)
        assert met.status is PrereqStatus.SATISFIED and met.reasons == [MET_BY_PLACEMENT]
    plan = _plan(s, student)
    placed = _placed(plan)
    assert not {"01:640:026", "01:640:115"} & set(placed)     # nothing below 151 planned
    calc = placed["01:640:151"]          # placement never satisfies the requirement itself
    assert calc.prerequisite.state is EligibilityState.SATISFIED_BY_HISTORY
    assert Reason.PREREQUISITES_MET_BY_PLACEMENT in calc.reasons
    assert plan.placement is not None and plan.placement.course == "01:640:151"
    assert plan.placement.source_url.startswith("https://math.sas.rutgers.edu/")


def test_a_placement_below_the_course_leaves_the_prerequisite_to_decide(placement_world):
    s = placement_world
    student = _fresh(s, "01:640:026")
    # A placement IS stated, so nothing is "unknown": the prerequisite decides.
    assert _check(s, student, "01:640:115").status is PrereqStatus.UNSATISFIED
    done = _check(s, student, "01:640:115", passed=["01:640:026"])
    assert done.status is PrereqStatus.SATISFIED and REQUIRES_PLACEMENT not in done.reasons


def test_placement_never_touches_other_subjects(placement_world):
    s = placement_world
    student = _fresh(s, "01:640:151")
    # 14:332:252 (ECE) is in the sample; a math placement says nothing about it.
    check = _check(s, student, "14:332:252")
    assert MET_BY_PLACEMENT not in check.reasons
