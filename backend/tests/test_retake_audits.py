"""Retakes through the real audit path, scenarios and the audit cache (Phase 6.2).

Engine-level retake behaviour is pinned in
ingestion/tests/test_retakes.py on real CS data. These tests cover what only
PostgreSQL and the service layer can: the cached actual audit, a read-only
scenario audit, and the fact that an audit cached under the previous engine
version - when duplicates were still allocated - is never served again.
"""

from __future__ import annotations

import decimal

import pytest
from sqlalchemy import select

from app.models import Course, Student, StudentCourse

from .test_multi_program import _session, _world, requires_db


@pytest.fixture
def retake_world():
    """Student A from the Phase 6.0 world, with course X attempted twice."""
    with _session() as session:
        world = _world(session)
        x = session.scalar(select(Course).where(
            Course.course_string == world["courses"]["X"]))
        session.add(StudentCourse(student_id=world["a"], course_id=x.id, term_code="20261",
                                  status="completed", grade="B",
                                  credits_earned=decimal.Decimal("3.0")))
        session.commit()
    return world


def _applied(result, course_string):
    return [a for a in result.allocation if a.course.course_string == course_string]


@requires_db
def test_the_real_audit_allocates_a_repeated_course_once(retake_world) -> None:
    from app.services.audit.cached_audit import audit_with_cache

    with _session() as session:
        student = session.get(Student, retake_world["a"])
        result, _ = audit_with_cache(session, student, use_cache=False)

    x = retake_world["courses"]["X"]
    assert [a.requirement_code for a in _applied(result, x)] == ["P1_X"]
    # Phase 6.4 (SAS repeated-course policy): the original A counts; the
    # later B is E credit.
    assert (_applied(result, x)[0].term_code, _applied(result, x)[0].earned_grade) == ("20259", "A")
    assert any(f.code == "repeated_course" for f in result.findings)


@requires_db
def test_a_scenario_audit_allocates_a_repeated_course_once(retake_world) -> None:
    from app.services.scenarios import run_scenario_audit

    with _session() as session:
        student = session.get(Student, retake_world["a"])
        scenario = run_scenario_audit(session, student, retake_world["keys"]["p2"])

    x = retake_world["courses"]["X"]
    assert [a.requirement_code for a in _applied(scenario.audit, x)] == ["P2_COMPUTING"]


@requires_db
def test_attempt_rows_are_untouched_by_audits(retake_world) -> None:
    from app.services.audit.cached_audit import audit_with_cache
    from app.services.scenarios import run_scenario_audit

    with _session() as session:
        student = session.get(Student, retake_world["a"])
        audit_with_cache(session, student)
        run_scenario_audit(session, student, retake_world["keys"]["p2"])
        session.commit()
        rows = session.scalars(select(StudentCourse.term_code).where(
            StudentCourse.student_id == student.id)).all()
    assert sorted(rows).count("20261") == 1 and len(rows) == 6


@requires_db
def test_an_audit_cached_before_the_fix_is_never_served(retake_world, monkeypatch) -> None:
    """Write a DOUBLE-counted audit under engine version 6.0.0, as the cache
    would have held before Phase 6.2, then read with the current engine: it
    must be a miss, recomputed, and single-counted."""
    from app.services.audit import cache as cache_module
    from app.services.audit.cache import AuditCacheKey, write_cached_audit
    from app.services.audit.cached_audit import audit_with_cache

    x = retake_world["courses"]["X"]
    with _session() as session:
        student = session.get(Student, retake_world["a"])
        fresh, _ = audit_with_cache(session, student, use_cache=False)
        doctored = fresh.model_copy(deep=True)
        doctored.allocation = [*doctored.allocation, *_applied(fresh, x)]   # the old bug
        monkeypatch.setattr(cache_module, "AUDIT_ENGINE_VERSION", "6.0.0")
        write_cached_audit(session, student, AuditCacheKey.compute(session, student), doctored)
        session.commit()
        monkeypatch.undo()

        served, from_cache = audit_with_cache(session, student)

    assert cache_module.AUDIT_ENGINE_VERSION == "6.4.0"
    assert from_cache is False
    assert len(_applied(served, x)) == 1
