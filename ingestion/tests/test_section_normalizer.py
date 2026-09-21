"""Section normalizer tests: RawSocSection -> NormalizedSection."""

from __future__ import annotations

import pytest
from coursepilot_ingestion.normalizers.sections import SocSectionNormalizer
from coursepilot_ingestion.parsers.sections import SocSectionParser
from coursepilot_ingestion.section_schemas import NormalizedMeeting, NormalizedSection
from pydantic import ValidationError

TERM = "20269"


def _by_index(payload: bytes) -> dict[str, NormalizedSection]:
    parsed = SocSectionParser().parse(payload)
    n = SocSectionNormalizer(term_code=TERM)
    return {s.index: n.normalize(p, s) for p, s in parsed.sections}


def test_normalizes_the_whole_fixture(section_payload_bytes: bytes) -> None:
    out = _by_index(section_payload_bytes)

    assert len(out) == 15
    assert all(s.term_code == TERM for s in out.values())


def test_meetings_get_stable_ordinals(section_payload_bytes: bytes) -> None:
    # Ordinals are the natural key for meetings, so they must be dense and
    # start at 0 - a gap would make the key meaningless.
    section = _by_index(section_payload_bytes)["15777"]

    assert [m.meeting_index for m in section.meetings] == [0, 1, 2, 3, 4]


def test_tba_meeting_has_no_day_or_time(section_payload_bytes: bytes) -> None:
    # 36.8% of real meetings are TBA. They must land as NULL, never as a
    # placeholder string that later looks like real scheduling data.
    section = _by_index(section_payload_bytes)["10064"]
    meeting = section.meetings[0]

    assert meeting.meeting_day is None
    assert meeting.start_time_military is None
    assert meeting.end_time_military is None
    assert meeting.is_tba is True


def test_timed_meeting_keeps_military_time(section_payload_bytes: bytes) -> None:
    section = _by_index(section_payload_bytes)["10052"]
    meeting = section.meetings[0]

    assert meeting.meeting_day in {"M", "T", "W", "H", "F", "S", "U"}
    assert meeting.start_time_military is not None
    assert len(meeting.start_time_military) == 4
    assert meeting.start_time_military.isdigit()


def test_duplicate_instructor_names_are_both_kept(section_payload_bytes: bytes) -> None:
    # 01:447:380 lists 'GLODOWSKI TROTT' twice in the real payload. Ordinals
    # are the key precisely so this is preserved rather than deduplicated -
    # we cannot tell whether it is one person or two.
    section = _by_index(section_payload_bytes)["12326"]

    assert len(section.instructors) == 2
    assert section.instructors[0].name == section.instructors[1].name
    assert [i.instructor_index for i in section.instructors] == [0, 1]


def test_section_with_no_instructors(section_payload_bytes: bytes) -> None:
    section = _by_index(section_payload_bytes)["10111"]

    assert section.instructors == []


def test_cross_listings_are_normalized(section_payload_bytes: bytes) -> None:
    section = _by_index(section_payload_bytes)["10052"]

    assert len(section.cross_listings) == 1
    xl = section.cross_listings[0]
    assert xl.registration_index == "10053"
    assert xl.subject_code == "074"
    # SOC's two-space "no supplement" must be normalized here too.
    assert xl.supplement_code == ""


def test_course_natural_key_matches_the_course_model(section_payload_bytes: bytes) -> None:
    out = _by_index(section_payload_bytes)

    lecture = out["13352"]
    lab = out["13361"]

    assert lecture.course_natural_key == ("01", "750", "193", "")
    assert lab.course_natural_key == ("01", "750", "193", "LB")
    # Different courses, so a section must never be shared between them.
    assert lecture.course_natural_key != lab.course_natural_key


def test_campus_pair_shares_a_course_but_not_an_offering(section_payload_bytes: bytes) -> None:
    out = _by_index(section_payload_bytes)
    nb, ob = out["19370"], out["19371"]

    assert nb.course_natural_key == ob.course_natural_key
    assert nb.offering_natural_key != ob.offering_natural_key
    assert {nb.campus_code, ob.campus_code} == {"NB", "OB"}


def test_non_numeric_section_number_survives(section_payload_bytes: bytes) -> None:
    # 344 distinct non-numeric section numbers exist in real data.
    section = _by_index(section_payload_bytes)["10152"]

    assert section.section_number == "C2"


def test_closed_section_keeps_false_open_status(section_payload_bytes: bytes) -> None:
    section = _by_index(section_payload_bytes)["13352"]

    assert section.open_status is False


def test_end_before_start_is_flagged_not_dropped(section_payload_bytes: bytes) -> None:
    # 07:966:123 has a real 1100-1100 meeting. It must normalize successfully;
    # the anomaly is surfaced by the validator, not by discarding the section.
    section = _by_index(section_payload_bytes)["15777"]
    flagged = [m for m in section.meetings if m.crosses_midnight_or_zero_length]

    assert len(flagged) >= 1


def test_empty_strings_become_none(section_payload_bytes: bytes) -> None:
    # SOC returns '' rather than null. Collapsing to None keeps "absent"
    # distinguishable from "present but blank".
    for section in _by_index(section_payload_bytes).values():
        for value in (section.subtitle, section.section_notes, section.comments_text):
            assert value != ""


def test_online_meeting_mode_is_preserved(section_payload_bytes: bytes) -> None:
    section = _by_index(section_payload_bytes)["10056"]
    modes = {m.meeting_mode_code for m in section.meetings}

    assert "90" in modes  # ONLINE INSTRUCTION(INTERNET)


# --------------------------------------------------------------------------
# schema-level rejection
# --------------------------------------------------------------------------


def test_rejects_invalid_meeting_day() -> None:
    with pytest.raises(ValidationError, match="not in observed Rutgers values"):
        NormalizedMeeting(meeting_index=0, meeting_day="X", meeting_mode_code="02")


def test_rejects_malformed_time() -> None:
    with pytest.raises(ValidationError, match="4-digit military time"):
        NormalizedMeeting(
            meeting_index=0,
            meeting_mode_code="02",
            start_time_military="9am",
            end_time_military="1000",
        )


def test_rejects_half_populated_times() -> None:
    # Measured: 0 of 17,457 meetings have one time without the other, so a
    # half-populated pair means the source shape changed.
    with pytest.raises(ValidationError, match="both be present or both absent"):
        NormalizedMeeting(
            meeting_index=0, meeting_mode_code="02", start_time_military="1000"
        )


def test_rejects_negative_meeting_ordinal() -> None:
    with pytest.raises(ValidationError):
        NormalizedMeeting(meeting_index=-1, meeting_mode_code="02")
