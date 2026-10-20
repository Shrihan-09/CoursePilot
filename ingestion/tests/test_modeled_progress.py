"""Fabricated student integrations for the read-only progress contract."""

from fractions import Fraction

from app.services.progress import build_progress

from .test_planning_engine import CS_DONE, _student, cs  # noqa: F401


def test_current_work_is_not_completed_and_read_does_not_write(cs):  # noqa: F811
    student = _student(
        cs, [("01:198:111", "20259"), ("01:198:112", "20269", "in_progress")]
    )
    result = build_progress(cs, student)
    major = next(m for m in result.metrics if m.scope == "primary_program")
    completed = Fraction(
        major.completed.units.numerator, major.completed.units.denominator
    )
    current = Fraction(
        major.projected_current.units.numerator,
        major.projected_current.units.denominator,
    )
    assert current > completed
    assert result.projection_state == "absent"
    assert result.graduation_eligibility == "not_assessed"
    assert not cs.new and not cs.dirty and not cs.deleted
    assert result.academic_context["complete"] is False


def test_missing_school_context_is_unavailable_not_fabricated_percentage(cs):  # noqa: F811
    result = build_progress(cs, _student(cs, []))
    school = next(m for m in result.metrics if m.scope == "school_core")
    assert school.availability == "unavailable"
    assert school.completed.percent is None
    assert result.coverage == "partial"
    assert "school_requirements" in result.academic_context["missing_scopes"]


def test_progress_denominator_is_stable_as_history_changes(cs):  # noqa: F811
    a = build_progress(cs, _student(cs, []))
    b = build_progress(cs, _student(cs, CS_DONE))
    assert [m.denominator_units for m in a.metrics] == [
        m.denominator_units for m in b.metrics
    ]
    assert b.metrics[1].completed.percent > a.metrics[1].completed.percent


def test_in_progress_work_never_changes_the_completed_segment(cs):  # noqa: F811
    only = build_progress(cs, _student(cs, [("01:198:111", "20259")]))
    more = build_progress(
        cs, _student(cs, [("01:198:111", "20259"), ("01:198:112", "20269", "in_progress")])
    )
    for a, b in zip(only.metrics, more.metrics, strict=True):
        assert (a.completed, a.remaining, a.percentage_kind) == (
            b.completed,
            b.remaining,
            b.percentage_kind,
        ), a.scope
