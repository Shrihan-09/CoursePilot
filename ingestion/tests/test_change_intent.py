"""Phase 7.2.4 course-change intent: deterministic, read-only, consequences stated."""

from __future__ import annotations

import pytest
from app.domain.plan_change import ChangeIntent
from app.services.planning.change_intent import CourseNotInPlan, change_options
from app.services.planning.offerings import PLACEABLE, OfferingIndex
from app.services.planning.service import generate_plan

from .test_planning_engine import START, _student, cs  # noqa: F401


@pytest.fixture
def early(cs):  # noqa: F811
    """A fabricated student early in the CS major: the plan has prerequisite chains."""
    student = _student(cs, [("01:198:111", "20259"), ("01:640:151", "20259")])
    plan = generate_plan(cs, student, start_term=START)
    chained = next(pc for t in plan.terms for pc in t.courses
                   if any(d.course == pc.course for t2 in plan.terms for o in t2.courses
                          for d in o.prerequisite.conditional_on))
    return cs, student, plan, chained


def _opt(result, intent):
    return next(o for o in result.options if o.intent is intent)


def test_analysis_is_read_only_and_states_every_intent(early):
    session, student, plan, pc = early
    result = change_options(session, student, plan, pc.course, pc.term_code)
    assert not session.new and not session.dirty and not session.deleted
    assert result.applied is False
    assert {o.intent for o in result.options} == set(ChangeIntent)


def test_removing_states_open_requirements_and_broken_dependents(early):
    session, student, plan, pc = early
    result = change_options(session, student, plan, pc.course, pc.term_code)
    remove = _opt(result, ChangeIntent.REMOVE_AND_LEAVE_REQUIREMENT_OPEN)
    assert result.dependents, "the fixture course is a prerequisite of a planned course"
    for dep in result.dependents:
        assert f"loses_prerequisite_for:{dep}" in remove.consequences
    for code in result.requirements:
        assert f"requirement_left_open:{code}" in remove.consequences


def test_moves_respect_prerequisite_order_and_offerings(early):
    session, student, plan, pc = early
    result = change_options(session, student, plan, pc.course, pc.term_code)
    term_of = {o.course: t.term_code for t in plan.terms for o in t.courses}
    earliest_dependent = min(term_of[d] for d in result.dependents)
    index = OfferingIndex(session, {pc.course})
    for term in _opt(result, ChangeIntent.MOVE_TO_ANOTHER_TERM).candidates:
        assert term < earliest_dependent and term != pc.term_code
        assert index.evidence(pc.course, term).kind in PLACEABLE


def test_replacements_are_listed_never_chosen_or_assumed_eligible(early):
    session, student, plan, _ = early
    in_plan = {o.course for t in plan.terms for o in t.courses}
    for term in plan.terms:
        for course in term.courses:
            replace = _opt(change_options(session, student, plan, course.course, term.term_code),
                           ChangeIntent.REPLACE_SAME_REQUIREMENT)
            for alt in replace.replacements:
                assert alt.course not in in_plan and alt.course != course.course
                assert alt.eligibility_verified is False
                assert alt.offering_status in ("confirmed", "historically_likely")


def test_section_changes_need_real_section_data(early):
    session, student, plan, pc = early
    result = change_options(session, student, plan, pc.course, pc.term_code)
    for intent in (ChangeIntent.FIND_DIFFERENT_SECTION, ChangeIntent.CHANGE_MODALITY):
        option = _opt(result, intent)
        assert not option.available and option.reason == "no_section_data_for_term"


def test_a_course_not_in_the_plan_is_refused(early):
    session, student, plan, _ = early
    with pytest.raises(CourseNotInPlan):
        change_options(session, student, plan, "01:999:999", START)
