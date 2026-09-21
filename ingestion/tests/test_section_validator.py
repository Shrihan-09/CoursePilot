"""Section validator tests.

As with courses, the highest-value cases are the ones that must FAIL - plus,
here, two that must deliberately NOT fail.
"""

from __future__ import annotations

import pytest

from coursepilot_ingestion.normalizers.sections import SocSectionNormalizer
from coursepilot_ingestion.parsers.sections import SocSectionParser
from coursepilot_ingestion.section_schemas import (
    NormalizedMeeting,
    NormalizedSection,
)
from coursepilot_ingestion.validators.section import Level, SectionValidator
from pydantic import ValidationError

TERM = "20269"


def _section(**overrides) -> NormalizedSection:
    base = dict(
        term_code=TERM,
        index_number="10052",
        offering_unit_code="01",
        subject_code="013",
        course_number="120",
        supplement_code="",
        campus_code="NB",
        section_number="01",
        open_status=True,
    )
    base.update(overrides)
    return NormalizedSection(**base)


def _normalized(payload: bytes) -> list[NormalizedSection]:
    parsed = SocSectionParser().parse(payload)
    n = SocSectionNormalizer(term_code=TERM)
    return [n.normalize(p, s) for p, s in parsed.sections]


# --------------------------------------------------------------------------
# the real fixture must pass
# --------------------------------------------------------------------------


def test_real_fixture_passes_validation(section_payload_bytes: bytes) -> None:
    outcome = SectionValidator().validate(_normalized(section_payload_bytes))

    assert outcome.rejected == [], outcome.error_messages
    assert len(outcome.valid) == 15


# --------------------------------------------------------------------------
# rules that must NOT exist
# --------------------------------------------------------------------------


def test_end_before_start_warns_but_does_not_reject(section_payload_bytes: bytes) -> None:
    # Three real Rutgers meetings have end <= start. Rejecting them would
    # discard authentic sections over a source quirk.
    outcome = SectionValidator().validate(_normalized(section_payload_bytes))

    assert any("end" in w.message and "<= start" in w.message for w in outcome.warnings)
    assert all(s.index_number != "15777" for s, _ in outcome.rejected)


def test_zero_length_meeting_is_accepted() -> None:
    section = _section(
        meetings=[
            NormalizedMeeting(
                meeting_index=0,
                meeting_day="S",
                start_time_military="1100",
                end_time_military="1100",
                meeting_mode_code="07",
            )
        ]
    )
    outcome = SectionValidator().validate([section])

    assert len(outcome.valid) == 1
    assert any(w.field_name == "meeting.time" for w in outcome.warnings)


# --------------------------------------------------------------------------
# rejections
# --------------------------------------------------------------------------


def test_schema_rejects_blank_parent_identity() -> None:
    # This is caught one layer up: NormalizedSection cannot even be built with
    # a blank subject_code, so such a record never reaches the validator.
    with pytest.raises(ValidationError):
        _section(subject_code=" ")


def test_validator_still_rejects_incomplete_parent_identity() -> None:
    # Defense in depth. `model_construct` bypasses Pydantic the way a future
    # refactor or an alternate construction path might, so the validator's own
    # guard is exercised rather than assumed.
    section = NormalizedSection.model_construct(
        term_code=TERM,
        index_number="10052",
        offering_unit_code="01",
        subject_code="",
        course_number="120",
        supplement_code="",
        campus_code="NB",
        section_number="01",
        open_status=True,
        meetings=[],
        instructors=[],
        cross_listings=[],
    )
    outcome = SectionValidator().validate([section])

    assert outcome.valid == []
    assert "incomplete parent course identity" in outcome.error_messages[0]


def test_rejects_duplicate_registration_index_within_a_term() -> None:
    # The natural key is (term_code, index_number). A collision means our
    # identity assumption is wrong, and we cannot tell which record is right.
    a = _section(index_number="10052", section_number="01")
    b = _section(index_number="10052", section_number="02", course_number="121")

    outcome = SectionValidator().validate([a, b])

    assert outcome.valid == []
    assert len(outcome.rejected) == 2
    assert any("reused within term" in m for m in outcome.error_messages)


def test_rejects_duplicate_section_number_within_one_offering() -> None:
    a = _section(index_number="10052", section_number="01")
    b = _section(index_number="10053", section_number="01")

    outcome = SectionValidator().validate([a, b])

    # BOTH sections must be rejected: in a collision we cannot tell which one
    # is correct, so keeping either would silently store possibly-wrong data.
    assert outcome.valid == []
    assert len(outcome.rejected) == 2
    assert any("reused within one offering" in m for m in outcome.error_messages)


def test_rejects_duplicate_meeting_ordinals() -> None:
    section = _section(
        meetings=[
            NormalizedMeeting(meeting_index=0, meeting_mode_code="02"),
            NormalizedMeeting(meeting_index=0, meeting_mode_code="03"),
        ]
    )
    outcome = SectionValidator().validate([section])

    assert outcome.valid == []
    assert "duplicate meeting_index" in outcome.error_messages[0]


def test_schema_rejects_empty_section_number() -> None:
    with pytest.raises(ValidationError):
        _section(section_number=" ")


# --------------------------------------------------------------------------
# warnings
# --------------------------------------------------------------------------


def test_warns_on_unknown_campus() -> None:
    outcome = SectionValidator().validate([_section(campus_code="UNKNOWN")])

    assert len(outcome.valid) == 1
    assert any(w.field_name == "campus_code" for w in outcome.warnings)


def test_warns_when_meeting_count_exceeds_observed_maximum() -> None:
    section = _section(
        meetings=[
            NormalizedMeeting(meeting_index=i, meeting_mode_code="02") for i in range(6)
        ]
    )
    outcome = SectionValidator().validate([section])

    assert len(outcome.valid) == 1
    assert any("exceeds the observed maximum" in w.message for w in outcome.warnings)


def test_error_messages_name_the_section_and_the_problem() -> None:
    # At ~12,000 sections, "invalid section" is useless; the message must name
    # the offending index and what was wrong.
    a = _section(index_number="10052", section_number="01")
    b = _section(index_number="10053", section_number="01")
    message = SectionValidator().validate([a, b]).error_messages[0]

    assert "1005" in message
    assert "section_number" in message
    assert Level.ERROR.value in message
