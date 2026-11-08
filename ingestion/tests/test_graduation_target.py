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


def test_a_stated_first_semester_is_the_entry_term_when_the_record_has_none(cs):  # noqa: F811
    """10a follow-up: a brand-new freshman (nothing uploaded) who said they
    start Fall 2026 is counted from Fall 2026, not from the first planned term."""
    student = _student(cs, [])
    student.first_term = "20269"
    target = _plan(cs, student).graduation_target
    assert (target.entry_term, target.term_code) == ("20269", "20301")   # 8th: Spring 2030
    assert "the first semester you told us, Fall 2026" in target.basis


def test_a_stated_first_semester_wins_over_an_earlier_record_term_and_says_so(cs):  # noqa: F811
    """Correctness fixes item 1: the student's answer wins over the earliest
    record term (a mis-dated transcript row must not move graduation), and the
    conflict is shown - on the target and as a plan issue."""
    student = _student(cs, CLASS_OF_2028)                 # record starts Fall 2024
    student.first_term = "20259"                          # the student says Fall 2025
    plan = _plan(cs, student)
    target = plan.graduation_target
    assert (target.entry_term, target.term_code) == ("20259", "20291")   # 8th: Spring 2029
    assert target.record_first_term == "20249"
    assert "Fall 2024, before the first semester you told us (Fall 2025)" in target.conflict
    issue = next(i for i in plan.issues if i.code == "FIRST_SEMESTER_CONFLICT")
    assert issue.severity.value == "needs_confirmation"
    assert issue.details == {"stated_first_term": "20259", "record_first_term": "20249"}


def test_no_conflict_when_the_record_starts_at_the_stated_semester(cs):  # noqa: F811
    student = _student(cs, CLASS_OF_2028)
    student.first_term = "20249"
    plan = _plan(cs, student)
    assert plan.graduation_target.conflict is None
    assert not any(i.code == "FIRST_SEMESTER_CONFLICT" for i in plan.issues)


def test_a_course_recorded_before_its_prerequisite_is_flagged_never_changed(cs):  # noqa: F811
    """Correctness fixes item 1: Data Structures (01:198:112, prerequisite
    01:198:111) recorded a term BEFORE 01:198:111 is impossible as recorded:
    flagged NEEDS REVIEW, the record untouched."""
    from app.services.record_checks import term_order_conflicts

    student = _student(cs, [("01:198:112", "20249"), ("01:198:111", "20261")])
    [conflict] = term_order_conflicts(cs, student)
    assert (conflict.course, conflict.term_code) == ("01:198:112", "20249")
    assert conflict.later == (("01:198:111", "20261"),)
    assert conflict.message.startswith("NEEDS REVIEW - 01:198:112 is recorded in Fall 2024, "
                                       "before its prerequisite 01:198:111 (Spring 2026)")
    plan = _plan(cs, student)
    issue = next(i for i in plan.issues if i.code == "RECORD_TERM_ORDER_IMPOSSIBLE")
    assert issue.course == "01:198:112" and issue.severity.value == "needs_confirmation"
    assert issue.details["prerequisites_later"] == [{"course": "01:198:111", "term": "20261"}]


def test_a_record_in_prerequisite_order_is_not_flagged(cs):  # noqa: F811
    from app.services.record_checks import term_order_conflicts

    assert term_order_conflicts(cs, _student(cs, CLASS_OF_2028)) == []
