"""Validator tests.

The most valuable cases here are the ones that must FAIL. A validator only
ever exercised on good data has not been tested.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from coursepilot_ingestion.normalizers.soc import SocNormalizer
from coursepilot_ingestion.parsers.soc import SocParser
from coursepilot_ingestion.schemas import NormalizedCourse
from coursepilot_ingestion.validators.course import CourseValidator, Level
from pydantic import ValidationError


def _course(**overrides) -> NormalizedCourse:
    base = dict(
        offering_unit_code="01",
        subject_code="013",
        course_number="120",
        supplement_code="",
        course_string="01:013:120",
        title="LITERARY EGYPT",
        credits=Decimal(3),
        level="U",
        term_code="20269",
        campus_code="NB",
    )
    base.update(overrides)
    return NormalizedCourse(**base)


# --------------------------------------------------------------------------
# Pydantic-level rejection (schema invariants)
# --------------------------------------------------------------------------


def test_rejects_malformed_course_string() -> None:
    with pytest.raises(ValidationError, match="does not match the observed Rutgers format"):
        _course(course_string="CS-112")


def test_rejects_non_numeric_course_number() -> None:
    with pytest.raises(ValidationError, match="3-digit Rutgers course number"):
        _course(course_number="12A")


def test_rejects_negative_credits() -> None:
    with pytest.raises(ValidationError, match="cannot be negative"):
        _course(credits=Decimal(-1))


def test_rejects_implausible_credits() -> None:
    with pytest.raises(ValidationError, match="implausibly high"):
        _course(credits=Decimal(500))


def test_rejects_unknown_level() -> None:
    with pytest.raises(ValidationError, match="not in observed values"):
        _course(level="X")


def test_rejects_empty_title() -> None:
    with pytest.raises(ValidationError):
        _course(title="")


def test_rejects_unknown_field() -> None:
    # extra="forbid" on our own schema: a typo in a field name should be a
    # loud error, not a silently ignored value.
    with pytest.raises(ValidationError):
        _course(titel="typo")


def test_accepts_null_credits() -> None:
    # Legitimate for 11.1% of real records; must not be rejected.
    assert _course(credits=None).credits is None


# --------------------------------------------------------------------------
# Validator-level checks (cross-field and cross-record)
# --------------------------------------------------------------------------


def test_rejects_course_string_disagreeing_with_components() -> None:
    # Pydantic checks each field's shape; only the validator can catch that
    # the parts and the assembled string contradict each other.
    bad = _course(course_string="01:013:999", course_number="120")
    outcome = CourseValidator().validate([bad])

    assert outcome.valid == []
    assert len(outcome.rejected) == 1
    assert "disagrees with its components" in outcome.error_messages[0]


def test_warns_on_null_credits_without_rejecting() -> None:
    outcome = CourseValidator().validate([_course(credits=None)])

    assert len(outcome.valid) == 1
    assert any(w.field_name == "credits" and w.level is Level.WARNING for w in outcome.warnings)


def test_warns_when_duplicate_key_disagrees_on_credits() -> None:
    # Our whole schema rests on "the NB/OB duplicate pair agrees on
    # course-level attributes". If that ever stops being true we must be told,
    # rather than silently keeping whichever row was processed last.
    nb = _course(campus_code="NB", credits=Decimal(3))
    ob = _course(campus_code="OB", credits=Decimal(4))

    outcome = CourseValidator().validate([nb, ob])

    assert len(outcome.valid) == 2
    assert any("disagrees on credits" in str(w) for w in outcome.warnings)


def test_real_fixture_passes_validation(sample_payload_bytes: bytes) -> None:
    raws = SocParser().parse(sample_payload_bytes).courses
    normalizer = SocNormalizer(term_code="20269")
    outcome = CourseValidator().validate([normalizer.normalize(r) for r in raws])

    assert outcome.rejected == [], outcome.error_messages
    assert len(outcome.valid) == 10


def test_error_messages_name_the_field_and_the_value() -> None:
    # A message of "invalid course" is useless at 4,400 records.
    outcome = CourseValidator().validate([_course(course_string="01:013:999", course_number="120")])
    message = outcome.error_messages[0]

    assert "01:013:999" in message
    assert "course_string" in message
    assert "01:013:120" in message  # what was expected
