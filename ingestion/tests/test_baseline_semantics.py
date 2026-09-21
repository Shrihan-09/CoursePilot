"""Baseline semantics (Phase 4.5, Part A).

The baseline is what a regression-aware objective protects. These tests pin
its definition against the REAL evaluator and real courses:

    Baseline = audit over COMPLETED courses only, requirements SATISFIED

Production allocation behaviour is unchanged by this phase.

## Synthetic data

Requirement DEFINITIONS are SYNTHETIC. The COURSES are real.
"""

from __future__ import annotations

import json

from app.domain.audit import RequirementStatus
from app.models import ProgramVersion, Student, StudentCourse
from app.services.audit.baseline import (
    EARNED_STATUSES,
    Baseline,
    baseline_from_result,
    compute_baseline,
)
from app.services.audit.engine import DegreeAuditEngine
from coursepilot_ingestion.loaders.requirements import RequirementLoader
from sqlalchemy import select

from tests.test_degree_audit import _course, _find

C1 = "01:013:120"
C2 = "01:070:102"
C3 = "01:070:201"

_SOURCE = {
    "url": "synthetic://coursepilot/test/phase-4.5",
    "catalog_year": "2035-2036",
    "retrieved_at": "2026-09-21",
    "kind": "manual_curation",
    "curation_status": "synthetic",
}


def _load(session, requirements: list[dict]) -> ProgramVersion:
    definition = {
        "source": _SOURCE,
        "school": {"code": "SAS", "name": "School of Arts and Sciences", "campus_code": "NB"},
        "program": {"code": "994", "name": "Synthetic Program 4.5", "degree_type": "BA"},
        "program_version": {
            "catalog_year": "2035-2036",
            "total_credits_min": 12,
            "sharing_policy": "exclusive",
            "source_prose": "SYNTHETIC TEST DATA - not a Rutgers requirement.",
        },
        "requirements": requirements,
    }
    RequirementLoader(session).load(definition, json.dumps(definition).encode())
    session.commit()
    return session.scalar(
        select(ProgramVersion).where(ProgramVersion.catalog_year == "2035-2036")
    )


def _student(session, version, ref="baseline"):
    student = Student(
        external_ref=ref,
        catalog_year=version.catalog_year,
        program_version_id=version.id,
    )
    session.add(student)
    session.flush()
    return student


def _enroll(session, student, course_string, status, grade=None):
    course = _course(session, course_string)
    session.add(
        StudentCourse(
            student_id=student.id,
            course_id=course.id,
            term_code="20269",
            status=status,
            grade=grade,
            credits_earned=course.credits,
        )
    )
    session.flush()


_REQS = [
    {
        "code": "R_ONE",
        "name": "One course",
        "requirement_type": "choose_n",
        "min_count": 1,
        "sort_order": 0,
        "parent": None,
        "requirement_system": "core",
        "courses": [C1],
        "source_prose": "SYNTHETIC",
    },
    {
        "code": "R_TWO",
        "name": "Two courses",
        "requirement_type": "choose_n",
        "min_count": 2,
        "sort_order": 1,
        "parent": None,
        "requirement_system": "core",
        "courses": [C2, C3],
        "source_prose": "SYNTHETIC",
    },
]


# ==========================================================================
# completed courses establish the baseline
# ==========================================================================


def test_completed_courses_establish_the_baseline(cs_session) -> None:
    version = _load(cs_session, _REQS)
    student = _student(cs_session, version, "b1")
    _enroll(cs_session, student, C1, "completed", "A")
    cs_session.commit()

    baseline = compute_baseline(cs_session, student)

    assert isinstance(baseline, Baseline)
    assert baseline.satisfied == {"R_ONE"}


def test_planned_courses_never_enter_the_baseline(cs_session) -> None:
    """A planned course is an intention, not evidence - and must not be able
    to protect a requirement from regressing."""
    version = _load(cs_session, _REQS)
    student = _student(cs_session, version, "b2")
    _enroll(cs_session, student, C1, "planned")
    cs_session.commit()

    assert compute_baseline(cs_session, student).satisfied == frozenset()


def test_in_progress_courses_are_excluded_from_the_baseline(cs_session) -> None:
    """The distinction the evaluator already makes.

    An in-progress course can still be failed, so counting it would let the
    audit promise to protect a completion the student has not earned.
    """
    version = _load(cs_session, _REQS)
    student = _student(cs_session, version, "b3")
    _enroll(cs_session, student, C1, "in_progress")
    cs_session.commit()

    baseline = compute_baseline(cs_session, student)
    assert baseline.satisfied == frozenset()

    # ...while a NORMAL audit still reports it as provisionally satisfied.
    full = DegreeAuditEngine(cs_session).audit(student)
    assert _find(full, "R_ONE").status is RequirementStatus.PROVISIONALLY_SATISFIED


def test_mixed_record_counts_only_completed(cs_session) -> None:
    version = _load(cs_session, _REQS)
    student = _student(cs_session, version, "b4")
    _enroll(cs_session, student, C1, "completed", "A")
    _enroll(cs_session, student, C2, "in_progress")
    _enroll(cs_session, student, C3, "planned")
    cs_session.commit()

    baseline = compute_baseline(cs_session, student)
    assert baseline.satisfied == {"R_ONE"}
    assert "R_TWO" not in baseline.satisfied


def test_partial_progress_is_not_in_the_baseline(cs_session) -> None:
    """R_TWO needs two courses and has one. Partial progress is deliberately
    NOT protected - only completion is."""
    version = _load(cs_session, _REQS)
    student = _student(cs_session, version, "b5")
    _enroll(cs_session, student, C2, "completed", "A")
    cs_session.commit()

    baseline = compute_baseline(cs_session, student)
    assert baseline.satisfied == frozenset()


def test_baseline_is_deterministic(cs_session) -> None:
    version = _load(cs_session, _REQS)
    student = _student(cs_session, version, "b6")
    _enroll(cs_session, student, C1, "completed", "A")
    _enroll(cs_session, student, C2, "completed", "A")
    cs_session.commit()

    runs = [compute_baseline(cs_session, student).satisfied for _ in range(5)]
    assert all(r == runs[0] for r in runs)


def test_baseline_does_not_depend_on_any_previous_audit(cs_session) -> None:
    """The baseline is derived from stored student state, never from an
    earlier optimizer run - so running audits first cannot change it."""
    version = _load(cs_session, _REQS)
    student = _student(cs_session, version, "b7")
    _enroll(cs_session, student, C1, "completed", "A")
    cs_session.commit()

    before = compute_baseline(cs_session, student).satisfied
    for _ in range(3):
        DegreeAuditEngine(cs_session).audit(student)
    after = compute_baseline(cs_session, student).satisfied

    assert before == after == {"R_ONE"}


# ==========================================================================
# the statuses parameter changes the INPUT, never the rules
# ==========================================================================


def test_statuses_parameter_defaults_to_current_behaviour(cs_session) -> None:
    """Omitting `statuses` must audit exactly as before this phase."""
    version = _load(cs_session, _REQS)
    student = _student(cs_session, version, "b8")
    _enroll(cs_session, student, C1, "completed", "A")
    _enroll(cs_session, student, C2, "in_progress")
    cs_session.commit()

    default = DegreeAuditEngine(cs_session).audit(student)
    explicit = DegreeAuditEngine(cs_session).audit(
        student, statuses=frozenset({"completed", "in_progress", "planned"})
    )

    assert [a.requirement_code for a in default.allocation] == [
        a.requirement_code for a in explicit.allocation
    ]
    assert default.credits_applicable_to_degree == explicit.credits_applicable_to_degree


def test_completed_only_audit_still_applies_every_rule(cs_session) -> None:
    """The baseline audit is a narrower INPUT, not a looser ruleset.

    A failing grade earns no credit toward a requirement in a normal audit,
    and must not start doing so in a baseline audit.
    """
    version = _load(cs_session, _REQS)
    student = _student(cs_session, version, "b9")
    _enroll(cs_session, student, C1, "completed", "F")
    cs_session.commit()

    assert compute_baseline(cs_session, student).satisfied == frozenset()


def test_earned_statuses_is_completed_only() -> None:
    assert EARNED_STATUSES == frozenset({"completed"})


# ==========================================================================
# extraction helper
# ==========================================================================


def test_baseline_from_result_separates_provisional(cs_session) -> None:
    """Given a WIDER audit, provisional completions are reported separately
    rather than silently folded into the protected set."""
    version = _load(cs_session, _REQS)
    student = _student(cs_session, version, "b10")
    _enroll(cs_session, student, C1, "in_progress")
    cs_session.commit()

    result = DegreeAuditEngine(cs_session).audit(student)
    baseline = baseline_from_result(result)

    assert "R_ONE" not in baseline.satisfied
    assert "R_ONE" in baseline.provisional


def test_baseline_walks_nested_requirements(cs_session) -> None:
    """Group nodes are included, so a satisfied parent is protected too."""
    nested = [
        {
            "code": "ROOT",
            "name": "Root",
            "requirement_type": "all_of",
            "sort_order": 0,
            "parent": None,
            "requirement_system": "core",
            "source_prose": "SYNTHETIC",
        },
        {
            "code": "LEAF",
            "name": "Leaf",
            "requirement_type": "choose_n",
            "min_count": 1,
            "sort_order": 0,
            "parent": "ROOT",
            "requirement_system": "core",
            "courses": [C1],
            "source_prose": "SYNTHETIC",
        },
    ]
    version = _load(cs_session, nested)
    student = _student(cs_session, version, "b11")
    _enroll(cs_session, student, C1, "completed", "A")
    cs_session.commit()

    baseline = compute_baseline(cs_session, student)
    assert baseline.satisfied == {"ROOT", "LEAF"}
