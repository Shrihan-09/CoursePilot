"""Phase 7.3: the graduation target is counted from the student's ENTRY term.

The CS world of the planning tests (real SOC samples, real curated CS B.A.).
Students are fabricated.
"""

from __future__ import annotations

from app.domain.planning import Pace, PlanningConstraints
from app.services.planning.service import generate_plan

from .test_planning_engine import _student, cs  # noqa: F401

SPRING27 = "20271"
CLASS_OF_2028 = [("01:198:111", "20249"), ("01:640:151", "20249"), ("01:198:112", "20251")]


def _plan(session, student, **kw):
    return generate_plan(session, student, start_term=SPRING27,
                         constraints=PlanningConstraints(**{"pace": Pace.BALANCED, **kw}))


def test_on_time_counts_eight_semesters_from_the_entry_term(cs):  # noqa: F811
    """Entered Fall 2024 (class of 2028), planning from Spring 2027: Spring
    2027, Fall 2027 and Spring 2028 - not eight semesters from today."""
    plan = _plan(cs, _student(cs, CLASS_OF_2028))
    target = plan.graduation_target
    assert (target.term_code, target.entry_term, target.semesters) == ("20281", "20249", 8)
    assert "Fall 2024" in target.basis
    assert {t.term_code for t in plan.terms} <= {"20271", "20279", "20281"}
    issue = next(i for i in plan.issues if i.code == "GRADUATION_TARGET_NOT_MET")
    assert "Spring 2028" in issue.message and "has not been changed" in issue.message
    assert issue.details["terms"] == 3


def test_an_expected_graduation_term_overrides_the_pace(cs):  # noqa: F811
    plan = _plan(cs, _student(cs, CLASS_OF_2028), pace=Pace.LIGHTER,
                 expected_graduation_term="20281")
    assert plan.graduation_target.term_code == "20281"
    assert plan.graduation_target.basis == "your expected graduation term"
    assert all(t.term_code <= "20281" for t in plan.terms)


def test_early_and_no_rush_count_from_entry_too(cs):  # noqa: F811
    student = _student(cs, CLASS_OF_2028)
    assert _plan(cs, student, pace=Pace.FINISH_EARLIER).graduation_target.term_code == "20279"
    assert _plan(cs, student, pace=Pace.LIGHTER).graduation_target.term_code == "20291"  # 10th


def test_a_student_with_no_record_counts_from_the_first_planned_semester(cs):  # noqa: F811
    plan = _plan(cs, _student(cs, []))
    assert plan.graduation_target.term_code == "20309"          # 8th semester from Spring 2027
    assert plan.graduation_target.entry_term == SPRING27


def test_a_stateless_request_keeps_the_plain_horizon(cs):  # noqa: F811
    plan = generate_plan(cs, _student(cs, CLASS_OF_2028), start_term=SPRING27)
    assert plan.graduation_target is None
    assert not any(i.code == "GRADUATION_TARGET_NOT_MET" for i in plan.issues)
