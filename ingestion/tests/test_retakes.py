"""Repeated attempts of one course (Phase 6.2).

Found in Phase 6.1 and reproduced here BEFORE the fix: a student with two
passing attempts of 01:198:314 filled TWO of the five CS_ELECTIVES slots and
was credited 8.0 applicable credits, because the allocator treated every
(course, term) row as an independent course.

The invariant these tests hold the engine to:

    Multiple attempts of the same course identity never become multiple
    independent allocation identities. One course, at most one slot per
    requirement system, credited once - unless a repeat-for-credit rule is
    explicitly modeled (none is today).

Attempt history is never deleted: every StudentCourse row survives the audit.

All data is real: the curated CS B.A. definition and archived SOC records.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.models import Course, ProgramVersion, Student, StudentCourse
from app.services.audit.engine import DegreeAuditEngine
from sqlalchemy import func, select


def _student(session, attempts):
    version = session.scalar(select(ProgramVersion))
    student = Student(external_ref="retake", catalog_year=version.catalog_year,
                      program_version_id=version.id)
    session.add(student)
    session.flush()
    for course_string, term, status, grade in attempts:
        course = session.scalar(select(Course).where(
            Course.course_string == course_string, Course.supplement_code == ""))
        assert course is not None, f"fixture is missing {course_string}"
        session.add(StudentCourse(student_id=student.id, course_id=course.id,
                                  term_code=term, status=status, grade=grade,
                                  credits_earned=course.credits))
    session.commit()
    return student


def _node(result, code):
    def walk(nodes):
        for n in nodes:
            if n.requirement_code == code:
                return n
            hit = walk(n.children)
            if hit:
                return hit
    return walk(result.requirements)


def _allocations(result, course_string):
    return [a for a in result.allocation if a.course.course_string == course_string]


def test_a_course_passed_twice_fills_one_elective_slot(cs_session) -> None:
    """The Phase 6.1 reproduction, kept permanently."""
    student = _student(cs_session, [
        ("01:198:314", "20259", "completed", "B"),
        ("01:198:314", "20261", "completed", "A"),
    ])
    result = DegreeAuditEngine(cs_session).audit(student)
    electives = _node(result, "CS_ELECTIVES")

    assert electives.satisfied_count == 1
    assert [c.course_string for c in electives.allocated_courses] == ["01:198:314"]
    assert len(_allocations(result, "01:198:314")) == 1
    assert result.credits_applicable_to_degree == Decimal("4.0")
    assert result.credits_completed == Decimal("4.0")
    assert any(f.code == "repeated_course" for f in result.findings)


def test_fail_then_pass_counts_the_passing_attempt_once(cs_session) -> None:
    student = _student(cs_session, [
        ("01:198:314", "20259", "completed", "F"),
        ("01:198:314", "20261", "completed", "B"),
    ])
    result = DegreeAuditEngine(cs_session).audit(student)

    allocated = _allocations(result, "01:198:314")
    assert len(allocated) == 1 and allocated[0].term_code == "20261"
    assert result.credits_applicable_to_degree == Decimal("4.0")
    # The failed attempt is still reported, not hidden.
    assert any(f.code == "non_passing_grade" for f in result.findings)


def test_pass_then_retake_counts_the_original_pass(cs_session) -> None:
    """Phase 6.4 replaced "most recent passing attempt" with the SAS policy:
    "If [a student has earned a grade of C or better] and choose[s] to repeat
    the course, it must be repeated for E credit" - no degree credit. So the
    original A counts and the later C is E credit (Registration and Course
    Information, Repeating Courses; archived 2026-27)."""
    student = _student(cs_session, [
        ("01:198:314", "20259", "completed", "A"),
        ("01:198:314", "20261", "completed", "C"),
    ])
    result = DegreeAuditEngine(cs_session).audit(student)

    allocated = _allocations(result, "01:198:314")
    assert len(allocated) == 1
    assert (allocated[0].term_code, allocated[0].earned_grade) == ("20259", "A")


def test_an_in_progress_retake_after_a_pass_does_not_add_a_slot(cs_session) -> None:
    """Earned beats provisional: the completed pass is what is allocated."""
    student = _student(cs_session, [
        ("01:198:314", "20259", "completed", "B"),
        ("01:198:314", "20269", "in_progress", None),
    ])
    result = DegreeAuditEngine(cs_session).audit(student)

    allocated = _allocations(result, "01:198:314")
    assert len(allocated) == 1
    assert (allocated[0].status, allocated[0].term_code) == ("completed", "20259")
    assert result.credits_in_progress == Decimal("0")


def test_an_in_progress_retake_after_a_fail_is_provisional(cs_session) -> None:
    student = _student(cs_session, [
        ("01:198:314", "20259", "completed", "F"),
        ("01:198:314", "20269", "in_progress", None),
    ])
    result = DegreeAuditEngine(cs_session).audit(student)

    allocated = _allocations(result, "01:198:314")
    assert len(allocated) == 1
    assert (allocated[0].status, allocated[0].term_code) == ("in_progress", "20269")


def test_a_repeated_course_cannot_fill_two_requirements_in_one_system(cs_session) -> None:
    """01:198:344 is both CS_344 and a 300-level elective. Two attempts must not
    let it satisfy both - within the major system allocation is exclusive."""
    student = _student(cs_session, [
        ("01:198:344", "20259", "completed", "C"),
        ("01:198:344", "20261", "completed", "B"),
    ])
    result = DegreeAuditEngine(cs_session).audit(student)

    assert _node(result, "CS_344").status == "satisfied"
    assert "01:198:344" not in {c.course_string for c in
                                _node(result, "CS_ELECTIVES").allocated_courses}
    assert len(_allocations(result, "01:198:344")) == 1


def test_attempt_history_survives_the_audit(cs_session) -> None:
    student = _student(cs_session, [
        ("01:198:314", "20259", "completed", "F"),
        ("01:198:314", "20261", "completed", "B"),
        ("01:198:314", "20269", "in_progress", None),
    ])
    DegreeAuditEngine(cs_session).audit(student)
    rows = cs_session.scalar(select(func.count()).select_from(StudentCourse)
                             .where(StudentCourse.student_id == student.id))
    assert rows == 3


def test_retake_allocation_is_deterministic(cs_session) -> None:
    student = _student(cs_session, [
        ("01:198:314", "20261", "completed", "A"),
        ("01:198:314", "20259", "completed", "A"),
        ("01:198:336", "20259", "completed", "B"),
    ])
    first = DegreeAuditEngine(cs_session).audit(student).model_dump_json()
    for _ in range(3):
        assert DegreeAuditEngine(cs_session).audit(student).model_dump_json() == first


@pytest.mark.parametrize("grade", ["F", "NC", "W"])
def test_failed_attempts_alone_allocate_nothing(cs_session, grade) -> None:
    student = _student(cs_session, [
        ("01:198:314", "20259", "completed", grade),
        ("01:198:314", "20261", "completed", grade),
    ])
    result = DegreeAuditEngine(cs_session).audit(student)
    assert _allocations(result, "01:198:314") == []
