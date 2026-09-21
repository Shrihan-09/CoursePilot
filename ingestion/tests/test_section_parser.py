"""Section parser tests: raw SOC bytes -> (ParentCourseRef, RawSocSection)."""

from __future__ import annotations

import json

import pytest
from coursepilot_ingestion.parsers.sections import SocSectionParser


def test_parses_the_real_fixture(section_payload_bytes: bytes) -> None:
    result = SocSectionParser().parse(section_payload_bytes)

    assert result.failures == []
    assert len(result.sections) == 15


def test_carries_parent_course_identity(section_payload_bytes: bytes) -> None:
    # Sections are nested and carry no field naming their own course, so the
    # parser must attach the parent explicitly or the linkage is lost.
    result = SocSectionParser().parse(section_payload_bytes)
    parent, section = next(
        (p, s) for p, s in result.sections if s.index == "10052"
    )

    assert parent.course_string == "01:013:120"
    assert parent.offering_unit_code == "01"
    assert parent.subject_code == "013"
    assert parent.course_number == "120"
    assert parent.campus_code == "NB"


def test_supplement_code_is_stripped_to_match_course_key(section_payload_bytes: bytes) -> None:
    # SOC sends '  ' for "no supplement"; the Course natural key stores ''.
    # If the parser did not strip here, no section of a non-supplemented
    # course would ever match its offering.
    result = SocSectionParser().parse(section_payload_bytes)

    lecture = next(p for p, s in result.sections if s.index == "13352")
    lab = next(p for p, s in result.sections if s.index == "13361")

    assert lecture.supplement_code == ""
    assert lab.supplement_code == "LB"


def test_campus_pair_keeps_distinct_parents(section_payload_bytes: bytes) -> None:
    result = SocSectionParser().parse(section_payload_bytes)

    nb = next(p for p, s in result.sections if s.index == "19370")
    ob = next(p for p, s in result.sections if s.index == "19371")

    assert nb.course_string == ob.course_string == "16:400:513"
    assert {nb.campus_code, ob.campus_code} == {"NB", "OB"}


def test_preserves_nested_structures(section_payload_bytes: bytes) -> None:
    result = SocSectionParser().parse(section_payload_bytes)
    _, section = next((p, s) for p, s in result.sections if s.index == "15777")

    # 5 meeting patterns - the observed maximum.
    assert len(section.meetingTimes) == 5


def test_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        SocSectionParser().parse(b"<html>503</html>")


def test_rejects_payload_that_is_not_a_list() -> None:
    with pytest.raises(ValueError, match="JSON array"):
        SocSectionParser().parse(b'{"courses": []}')


def test_reports_sections_whose_parent_lacks_identity(section_payload: list[dict]) -> None:
    # A section whose parent has no natural key could never be linked to an
    # offering. It must be reported, not parsed into an unattachable record.
    broken = json.loads(json.dumps(section_payload[0]))
    del broken["offeringUnitCode"]

    result = SocSectionParser().parse(json.dumps([broken]).encode())

    assert result.sections == []
    assert len(result.failures) == len(broken["sections"])
    assert "offeringUnitCode" in result.failures[0].error


def test_bad_section_does_not_abort_the_batch(section_payload: list[dict]) -> None:
    payload = json.loads(json.dumps(section_payload))
    # `index` is required; removing it must cost exactly one section.
    del payload[0]["sections"][0]["index"]

    result = SocSectionParser().parse(json.dumps(payload).encode())

    assert len(result.failures) == 1
    assert len(result.sections) == 14
    assert "index" in result.failures[0].error


def test_unknown_section_fields_are_preserved(section_payload_bytes: bytes) -> None:
    # The SOC API is undocumented; a new field must not be silently dropped.
    result = SocSectionParser().parse(section_payload_bytes)
    _, section = result.sections[0]

    assert hasattr(section, "printed")
