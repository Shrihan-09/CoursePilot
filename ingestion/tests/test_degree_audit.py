"""Deterministic degree audit tests (Phase 3).

Run against the REAL curated Rutgers CS B.A. requirement tree and REAL course
records. The requirement structure is a human derivation from official catalog
prose (Rutgers publishes requirements as English, not as data), so these tests
check the engine, not the accuracy of the derivation - that is what
`source_prose` on every node exists for.

The valuable cases here are the ones that must NOT be satisfied, and the
allocation cases where a naive implementation gets the right count but the
wrong answer.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.domain.audit import AuditStatus, RequirementStatus
from app.models import Course, ProgramVersion, Requirement, Student, StudentCourse
from app.services.audit import DegreeAuditEngine
from app.services.audit.allocation import Candidate, Slot, allocate
from sqlalchemy import select

TERM = "20269"
CORE = ["01:198:111", "01:198:112", "01:198:205", "01:198:206", "01:198:211", "01:198:344"]
MATH = ["01:640:151", "01:640:152", "01:640:250"]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _course(session, course_string: str) -> Course:
    c = session.scalar(
        select(Course).where(
            Course.course_string == course_string, Course.supplement_code == ""
        )
    )
    assert c is not None, f"fixture is missing {course_string}"
    return c


def _student(session, catalog_year: str | None = None) -> Student:
    version = session.scalar(select(ProgramVersion))
    student = Student(
        external_ref="test-student",
        catalog_year=catalog_year or version.catalog_year,
        program_version_id=version.id,
    )
    session.add(student)
    session.flush()
    return student


def _enroll(session, student, course_string, status="completed", grade="A", term=TERM):
    course = _course(session, course_string)
    session.add(
        StudentCourse(
            student_id=student.id,
            course_id=course.id,
            term_code=term,
            status=status,
            grade=grade,
            credits_earned=course.credits,
        )
    )
    session.flush()


def _audit(session, student):
    session.commit()
    return DegreeAuditEngine(session).audit(student)


def _find(result, code: str):
    def walk(rs):
        for r in rs:
            if r.requirement_code == code:
                return r
            found = walk(r.children)
            if found:
                return found
        return None

    return walk(result.requirements)


# --------------------------------------------------------------------------
# requirement structure loaded correctly
# --------------------------------------------------------------------------


def test_curated_requirements_load(cs_session) -> None:
    reqs = cs_session.scalars(select(Requirement)).all()
    codes = {r.code for r in reqs}

    assert "CS_BA" in codes
    assert {"CS_CORE", "CS_MATH", "CS_ELECTIVES"} <= codes
    # 6 core + 3 math leaf courses
    assert len([r for r in reqs if r.requirement_type == "course"]) == 9


def test_every_requirement_carries_its_source_prose(cs_session) -> None:
    """A curated requirement with no prose behind it cannot be re-checked
    against Rutgers, which is the whole basis for trusting it."""
    for req in cs_session.scalars(select(Requirement)).all():
        if req.requirement_type == "course":
            continue  # leaf nodes inherit their group's prose
        assert req.source_prose, f"{req.code} has no source prose"
        assert req.curation_status == "curated_from_prose"


def test_no_courses_were_invented(cs_session) -> None:
    """Every eligibility row must point at a real ingested course."""
    from app.models import RequirementCourseOption

    for option in cs_session.scalars(select(RequirementCourseOption)).all():
        assert cs_session.get(Course, option.course_id) is not None


# --------------------------------------------------------------------------
# audit: basic statuses
# --------------------------------------------------------------------------


def test_empty_record_satisfies_nothing(cs_session) -> None:
    student = _student(cs_session)
    result = _audit(cs_session, student)

    assert result.status is AuditStatus.INCOMPLETE
    assert _find(result, "CS_111").status is RequirementStatus.UNSATISFIED
    assert result.credits_completed == Decimal(0)


def test_completed_course_satisfies_its_requirement(cs_session) -> None:
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:111")
    result = _audit(cs_session, student)

    req = _find(result, "CS_111")
    assert req.status is RequirementStatus.SATISFIED
    assert req.allocated_courses[0].course_string == "01:198:111"
    assert "01:198:111" in req.reason


def test_in_progress_is_provisional_not_satisfied(cs_session) -> None:
    """The distinction that matters: a student can still fail an in-progress
    course, so reporting it as satisfied would be a false promise."""
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:111", status="in_progress", grade=None)
    result = _audit(cs_session, student)

    assert _find(result, "CS_111").status is RequirementStatus.PROVISIONALLY_SATISFIED


def test_planned_course_satisfies_nothing(cs_session) -> None:
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:111", status="planned", grade=None)
    result = _audit(cs_session, student)

    assert _find(result, "CS_111").status is RequirementStatus.UNSATISFIED
    assert result.credits_completed == Decimal(0)


def test_failing_grade_does_not_satisfy(cs_session) -> None:
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:111", grade="F")
    result = _audit(cs_session, student)

    assert _find(result, "CS_111").status is RequirementStatus.UNSATISFIED
    assert any(f.code == "non_passing_grade" for f in result.findings)


def test_all_of_group_requires_every_child(cs_session) -> None:
    student = _student(cs_session)
    for cs in MATH[:2]:
        _enroll(cs_session, student, cs)
    result = _audit(cs_session, student)

    math = _find(result, "CS_MATH")
    assert math.status is RequirementStatus.PARTIALLY_SATISFIED
    assert math.satisfied_count == 2
    assert math.needed_count == 3


def test_all_requirements_satisfied_but_audit_is_indeterminate(cs_session) -> None:
    """Every modeled requirement satisfied is NOT the same as a complete degree.

    Updated in Phase 3.5. This test previously asserted COMPLETE, which was
    correct when the only thing evaluated was the requirement tree. Now the
    real CS residency rule is loaded and marked NOT_EVALUABLE (the student
    record carries no transfer provenance), so an authoritative rule remains
    unchecked and the honest verdict is INDETERMINATE.

    Reporting COMPLETE here would be a promise the data does not support.
    """
    student = _student(cs_session)
    for cs in CORE + MATH:
        _enroll(cs_session, student, cs)
    for cs in ["01:198:314", "01:198:323", "01:198:334", "01:198:336", "01:198:345"]:
        _enroll(cs_session, student, cs)

    result = _audit(cs_session, student)

    # The requirement tree really is fully satisfied...
    assert _find(result, "CS_CORE").status is RequirementStatus.SATISFIED
    assert _find(result, "CS_MATH").status is RequirementStatus.SATISFIED
    assert _find(result, "CS_ELECTIVES").status is RequirementStatus.SATISFIED

    # ...but the degree is not declared complete.
    assert result.status is AuditStatus.INDETERMINATE
    assert result.is_complete is False
    assert [r.rule_code for r in result.not_evaluable_rules] == ["CS_RESIDENCY"]
    assert result.has_indeterminate is True


# --------------------------------------------------------------------------
# choose-N and its measured constraints
# --------------------------------------------------------------------------


def test_choose_n_counts_progress(cs_session) -> None:
    student = _student(cs_session)
    for cs in ["01:198:314", "01:198:323"]:
        _enroll(cs_session, student, cs)
    result = _audit(cs_session, student)

    electives = _find(result, "CS_ELECTIVES")
    assert electives.needed_count == 5
    assert electives.satisfied_count == 2
    assert electives.status is RequirementStatus.PARTIALLY_SATISFIED


def test_sub_300_course_is_not_eligible_for_electives(cs_session) -> None:
    """01:198:104 is a CS course but below the 300 level, so the curated
    eligibility query must exclude it entirely."""
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:104")
    result = _audit(cs_session, student)

    assert _find(result, "CS_ELECTIVES").satisfied_count == 0
    assert any(c.course_string == "01:198:104" for c in result.unallocated_courses)


def test_unallocated_courses_are_reported_not_hidden(cs_session) -> None:
    student = _student(cs_session)
    _enroll(cs_session, student, "01:013:120")  # non-CS, not eligible for anything
    result = _audit(cs_session, student)

    assert any(c.course_string == "01:013:120" for c in result.unallocated_courses)


# --------------------------------------------------------------------------
# allocation - the cases a naive engine gets wrong
# --------------------------------------------------------------------------


def test_course_eligible_for_two_requirements_goes_to_the_scarce_one(cs_session) -> None:
    """THE regression for the bug found against real data.

    01:198:344 is eligible for the required CS_344 node AND for the 53-option
    elective pool. Maximum-cardinality matching alone is indifferent - both
    are one slot - and it filled an elective slot, leaving a REQUIRED course
    unsatisfied. Most-constrained-first ordering fixes it.
    """
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:344")
    result = _audit(cs_session, student)

    assert _find(result, "CS_344").status is RequirementStatus.SATISFIED
    assert _find(result, "CS_ELECTIVES").satisfied_count == 0
    allocated_to = [
        a.requirement_code for a in result.allocation if a.course.course_string == "01:198:344"
    ]
    assert allocated_to == ["CS_344"]


def test_a_course_is_allocated_at_most_once(cs_session) -> None:
    """No double-counting: eligibility is many-to-many, allocation is not."""
    student = _student(cs_session)
    for cs in CORE:
        _enroll(cs_session, student, cs)
    result = _audit(cs_session, student)

    keys = [(a.course.course_string, a.term_code) for a in result.allocation]
    assert len(keys) == len(set(keys))


def test_allocation_is_deterministic(cs_session) -> None:
    """Same inputs, same allocation - every time. Without this an audit could
    change between page loads."""
    student = _student(cs_session)
    for cs in ["01:198:314", "01:198:323", "01:198:334"]:
        _enroll(cs_session, student, cs)

    engine = DegreeAuditEngine(cs_session)
    cs_session.commit()
    runs = [
        [(a.course.course_string, a.requirement_code) for a in engine.audit(student).allocation]
        for _ in range(5)
    ]
    assert all(r == runs[0] for r in runs)


def test_matching_avoids_the_greedy_trap() -> None:
    """Pure unit test of the allocator.

    Course A fits requirements R1 and R2; course B fits only R1. A greedy
    pass that assigns A->R1 first leaves R2 empty even though A->R2, B->R1
    satisfies both.
    """
    slots = [
        Slot("R1", 0, sort_key=(0, "R1"), option_count=2),
        Slot("R2", 0, sort_key=(1, "R2"), option_count=1),
    ]
    candidates = [
        Candidate("A", sort_key=("A",), eligible_requirements=frozenset({"R1", "R2"})),
        Candidate("B", sort_key=("B",), eligible_requirements=frozenset({"R1"})),
    ]

    plan = allocate(slots, candidates)

    assert len(plan.by_slot) == 2, "both requirements should be satisfiable"
    assert plan.by_slot[("R1", 0)] == "B"
    assert plan.by_slot[("R2", 0)] == "A"


def test_matching_is_stable_under_input_reordering() -> None:
    slots = [
        Slot("R1", 0, sort_key=(0, "R1"), option_count=2),
        Slot("R2", 0, sort_key=(1, "R2"), option_count=1),
    ]
    a = Candidate("A", sort_key=("A",), eligible_requirements=frozenset({"R1", "R2"}))
    b = Candidate("B", sort_key=("B",), eligible_requirements=frozenset({"R1"}))

    assert allocate(slots, [a, b]).by_slot == allocate(slots, [b, a]).by_slot
    assert allocate(slots, [a, b]).by_slot == allocate(list(reversed(slots)), [a, b]).by_slot


# --------------------------------------------------------------------------
# catalog-year isolation
# --------------------------------------------------------------------------


def test_catalog_year_mismatch_is_blocking(cs_session) -> None:
    """A student must be audited against THEIR catalog year. Auditing against
    another year's rules is reported, never silently tolerated."""
    student = _student(cs_session, catalog_year="2019-2020")
    for cs in CORE + MATH:
        _enroll(cs_session, student, cs)

    result = _audit(cs_session, student)

    assert result.status is AuditStatus.INSUFFICIENT_DATA
    assert any(f.code == "catalog_year_mismatch" for f in result.findings)


# --------------------------------------------------------------------------
# result contract
# --------------------------------------------------------------------------


def test_result_carries_disclaimer_and_provenance(cs_session) -> None:
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:111")
    result = _audit(cs_session, student)

    assert result.disclaimers and "advisor" in result.disclaimers[0]
    assert _find(result, "CS_CORE").source_prose is not None


def test_credit_totals_are_computed(cs_session) -> None:
    student = _student(cs_session)
    _enroll(cs_session, student, "01:198:111")                      # 4 credits
    _enroll(cs_session, student, "01:198:112", status="in_progress", grade=None)
    result = _audit(cs_session, student)

    assert result.credits_completed == Decimal(4)
    assert result.credits_in_progress == Decimal(4)
    assert result.credits_required_min == Decimal(51)
    assert result.credits_remaining == Decimal(47)


def test_audit_status_is_derived_not_settable(cs_session) -> None:
    """`is_complete` must follow from the requirement results."""
    student = _student(cs_session)
    result = _audit(cs_session, student)
    assert result.is_complete is False
    with pytest.raises((AttributeError, ValueError)):
        result.is_complete = True  # type: ignore[misc]
