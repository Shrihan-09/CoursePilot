"""Program-level rule evaluation (Phase 3.5).

Covers the three real Rutgers CS clauses that the requirement tree cannot
express, plus the credit distinction they force.

The most important assertions here are the ones that refuse to report success:
a NOT_EVALUABLE rule must prevent a COMPLETE verdict even when every modeled
requirement is satisfied.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.audit import AuditStatus, RequirementStatus
from app.models import Course, ProgramRule, ProgramVersion, Student, StudentCourse
from app.services.audit import DegreeAuditEngine
from app.services.audit.rules import StudentCourseView, evaluate_rule, excluded_course_strings
from sqlalchemy import select

from tests.test_degree_audit import CORE, MATH, TERM, _audit, _course, _enroll, _find, _student


def _rule(session, code: str) -> ProgramRule:
    r = session.scalar(select(ProgramRule).where(ProgramRule.code == code))
    assert r is not None, f"fixture is missing rule {code}"
    return r


def _rule_result(result, code: str):
    return next((r for r in result.rules if r.rule_code == code), None)


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def test_program_rules_load_from_curated_fixture(cs_session) -> None:
    rules = cs_session.scalars(select(ProgramRule)).all()
    by_code = {r.code: r for r in rules}

    assert set(by_code) == {"CS_MAX_D", "CS_EXCLUDED", "CS_RESIDENCY"}
    assert by_code["CS_MAX_D"].rule_type == "max_grade_count"
    assert by_code["CS_EXCLUDED"].rule_type == "course_exclusion"
    assert by_code["CS_RESIDENCY"].rule_type == "residency"


def test_every_rule_carries_its_source_prose(cs_session) -> None:
    for rule in cs_session.scalars(select(ProgramRule)).all():
        assert rule.source_prose, f"{rule.code} has no source prose"
        assert rule.curation_status == "curated_from_prose"


def test_non_evaluable_rule_must_state_why(cs_session) -> None:
    """A rule marked unevaluable without a reason is a silent omission."""
    for rule in cs_session.scalars(select(ProgramRule)).all():
        if not rule.is_evaluable:
            assert rule.not_evaluable_reason


# --------------------------------------------------------------------------
# grade rule: "No more than one grade of D"
# --------------------------------------------------------------------------


def test_no_d_grades_satisfies_grade_rule(cs_session) -> None:
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:111", grade="A")
    result = _audit(cs_session, student)

    rule = _rule_result(result, "CS_MAX_D")
    assert rule.status is RequirementStatus.SATISFIED
    assert rule.observed_count == 0


def test_exactly_one_d_is_allowed(cs_session) -> None:
    """The prose permits one. Rejecting it would invent a stricter rule."""
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:111", grade="D")
    _enroll(cs_session, student, "01:198:112", grade="A")
    result = _audit(cs_session, student)

    rule = _rule_result(result, "CS_MAX_D")
    assert rule.status is RequirementStatus.SATISFIED
    assert rule.observed_count == 1
    assert rule.allowed_count == 1


def test_two_ds_violate_the_grade_rule(cs_session) -> None:
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:111", grade="D")
    _enroll(cs_session, student, "01:198:112", grade="D")
    result = _audit(cs_session, student)

    rule = _rule_result(result, "CS_MAX_D")
    assert rule.status is RequirementStatus.UNSATISFIED
    assert rule.observed_count == 2
    # The student must be told WHICH courses, not just that something is wrong.
    affected = {c.course_string for c in rule.affected_courses}
    assert affected == {"01:198:111", "01:198:112"}
    assert any(f.code == "program_rule_violated" for f in result.findings)


def test_violated_grade_rule_blocks_completion(cs_session) -> None:
    """Every requirement satisfied, but two Ds: not a complete degree."""
    student = _student(cs_session)
    for cs in CORE + MATH:
        _enroll(cs_session, student, cs, grade="A")
    for cs in ["01:198:314", "01:198:323", "01:198:334", "01:198:336", "01:198:345"]:
        _enroll(cs_session, student, cs, grade="A")
    # Downgrade two of them to D.
    for cs in ("01:198:111", "01:198:112"):
        course = _course(cs_session, cs)
        row = cs_session.scalar(
            select(StudentCourse).where(
                StudentCourse.student_id == student.id, StudentCourse.course_id == course.id
            )
        )
        row.grade = "D"
    result = _audit(cs_session, student)

    assert _rule_result(result, "CS_MAX_D").status is RequirementStatus.UNSATISFIED
    assert result.status is AuditStatus.INCOMPLETE
    assert result.is_complete is False


# --------------------------------------------------------------------------
# exclusion rule
# --------------------------------------------------------------------------


def test_excluded_course_earns_no_degree_credit(cs_session) -> None:
    """01:198:405 is excluded for declared majors.

    The course still exists and is still on the record - it simply does not
    count here. Deleting it would be wrong; it may count elsewhere.
    """
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:405", grade="A")  # 3 credits, excluded
    _enroll(cs_session, student, "01:198:111", grade="A")  # 4 credits, counts
    result = _audit(cs_session, student)

    assert result.credits_completed == Decimal(7)
    assert result.credits_applicable_to_degree == Decimal(4)
    assert result.credits_excluded == Decimal(3)
    assert [c.course_string for c in result.excluded_courses] == ["01:198:405"]


def test_excluded_course_still_exists_in_the_database(cs_session) -> None:
    """The exclusion is program-specific, not a property of the Course.

    Nothing on the Course row records the exclusion: it is not deleted, not
    flagged, not altered. The rule lives on the ProgramVersion, so the same
    course can count fully toward a different program.
    """
    from app.models import RequirementCourseOption

    course = _course(cs_session, "01:198:405")
    assert course is not None
    assert course.title  # untouched by the exclusion

    # It is even still ELIGIBLE for the elective requirement - eligibility is
    # curriculum-level. Only allocation and credit counting exclude it.
    options = cs_session.scalars(
        select(RequirementCourseOption).where(
            RequirementCourseOption.course_id == course.id
        )
    ).all()
    assert options, "405 should remain eligible; exclusion is applied at audit time"


def test_excluded_course_cannot_satisfy_a_requirement(cs_session) -> None:
    """405 is a 300+ CS course, so it is otherwise elective-eligible."""
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:405", grade="A")
    result = _audit(cs_session, student)

    assert _find(result, "CS_ELECTIVES").satisfied_count == 0
    allocated = {a.course.course_string for a in result.allocation}
    assert "01:198:405" not in allocated


def test_exclusion_rule_reports_which_courses(cs_session) -> None:
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:405", grade="A")
    result = _audit(cs_session, student)

    rule = _rule_result(result, "CS_EXCLUDED")
    # Not a violation - it describes how credit counts.
    assert rule.status is RequirementStatus.SATISFIED
    assert rule.observed_count == 1
    assert "01:198:405" in rule.reason
    assert any(f.code == "course_excluded_from_degree" for f in result.findings)


def test_remaining_credits_use_applicable_not_completed(cs_session) -> None:
    """A student who took an excluded course is NOT closer to graduating."""
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:405", grade="A")  # excluded, 3 credits
    result = _audit(cs_session, student)

    assert result.credits_completed == Decimal(3)
    assert result.credits_applicable_to_degree == Decimal(0)
    assert result.credits_remaining == result.credits_required_min


# --------------------------------------------------------------------------
# residency rule: authoritative but NOT evaluable
# --------------------------------------------------------------------------


def test_residency_rule_is_not_evaluable(cs_session) -> None:
    student = _student(cs_session)
    for cs in CORE:
        _enroll(cs_session, student, cs)
    result = _audit(cs_session, student)

    rule = _rule_result(result, "CS_RESIDENCY")
    assert rule.status is RequirementStatus.NOT_EVALUABLE
    assert "transfer" in rule.reason.lower()


def test_not_evaluable_rule_produces_a_warning_finding(cs_session) -> None:
    student = _student(cs_session)
    result = _audit(cs_session, student)

    finding = next(f for f in result.findings if f.code == "rule_not_evaluable")
    assert "advisor" in (finding.remediation or "").lower()


def test_not_evaluable_rule_prevents_complete(cs_session) -> None:
    """The headline guarantee of this phase."""
    student = _student(cs_session)
    for cs in CORE + MATH:
        _enroll(cs_session, student, cs)
    for cs in ["01:198:314", "01:198:323", "01:198:334", "01:198:336", "01:198:345"]:
        _enroll(cs_session, student, cs)
    result = _audit(cs_session, student)

    assert result.status is AuditStatus.INDETERMINATE
    assert result.is_complete is False
    assert result.has_indeterminate is True
    assert len(result.not_evaluable_rules) == 1


# --------------------------------------------------------------------------
# pure unit tests of the rule functions
# --------------------------------------------------------------------------


def _view(code, grade="A", status="completed", subject="198", unit="01", credits=Decimal(3)):
    from app.domain.audit import CourseRef

    return StudentCourseView(
        ref=CourseRef(course_id=code, course_string=code, credits=credits),
        course_string=code,
        subject_code=subject,
        offering_unit_code=unit,
        status=status,
        grade=grade,
        credits=credits,
    )


def test_rule_evaluation_is_pure(cs_session) -> None:
    """No database access - the same inputs always give the same result."""
    rule = _rule(cs_session, "CS_MAX_D")
    views = [_view("01:198:111", grade="D"), _view("01:198:112", grade="D")]

    first = evaluate_rule(rule, views)
    second = evaluate_rule(rule, views)

    assert first.status is second.status
    assert first.observed_count == second.observed_count == 2


def test_in_progress_d_does_not_count_yet(cs_session) -> None:
    """A grade only exists once a course is completed."""
    rule = _rule(cs_session, "CS_MAX_D")
    views = [_view("01:198:111", grade="D", status="in_progress")]

    assert evaluate_rule(rule, views).observed_count == 0


def test_excluded_course_strings_helper(cs_session) -> None:
    rules = list(cs_session.scalars(select(ProgramRule)).all())
    excluded = excluded_course_strings(rules)

    assert excluded == {
        "01:198:105",
        "01:198:107",
        "01:198:110",
        "01:198:142",
        "01:198:170",
        "01:198:405",
    }


def test_non_evaluable_rule_short_circuits(cs_session) -> None:
    """Evaluability is checked before any rule-specific logic, so a rule with
    plenty of matching courses still reports NOT_EVALUABLE."""
    rule = _rule(cs_session, "CS_RESIDENCY")
    views = [_view(f"01:198:{n}") for n in range(300, 320)]

    result = evaluate_rule(rule, views)
    assert result.status is RequirementStatus.NOT_EVALUABLE
    assert result.observed_count is None
