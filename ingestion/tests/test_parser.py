"""Parser tests: raw bytes -> RawSocCourse."""

from __future__ import annotations

import json

import pytest
from coursepilot_ingestion.parsers.soc import SocParser


def test_parses_the_real_fixture(sample_payload_bytes: bytes) -> None:
    result = SocParser().parse(sample_payload_bytes)
    assert result.failures == []
    assert len(result.courses) == 10
    assert all(c.courseString for c in result.courses)


def test_preserves_unknown_fields(sample_payload_bytes: bytes) -> None:
    # The SOC API is undocumented and unversioned. If Rutgers adds a field we
    # must not silently discard it, so RawSocCourse allows extras.
    result = SocParser().parse(sample_payload_bytes)
    course = result.courses[0]
    assert hasattr(course, "campusCode")


def test_rejects_non_json() -> None:
    # A corrupt payload is fatal: there are no records to attribute failures
    # to, so this raises rather than returning a partial result.
    with pytest.raises(ValueError, match="not valid JSON"):
        SocParser().parse(b"<html>503 Service Unavailable</html>")


def test_rejects_payload_that_is_not_a_list() -> None:
    with pytest.raises(ValueError, match="JSON array"):
        SocParser().parse(b'{"courses": []}')


def test_reports_bad_records_without_losing_good_ones(sample_payload: list[dict]) -> None:
    # One malformed record must not abort the batch. This is the difference
    # between losing 1 course and losing 4,400.
    broken = dict(sample_payload[0])
    del broken["courseString"]
    payload = json.dumps([sample_payload[0], broken, sample_payload[1]]).encode()

    result = SocParser().parse(payload)

    assert len(result.courses) == 2
    assert len(result.failures) == 1
    assert "courseString" in result.failures[0].error
    assert result.failures[0].index == 1


def test_credits_null_and_fractional_survive_parsing(sample_payload_bytes: bytes) -> None:
    # 11.1% of real records have null credits and 91 have fractional values.
    # A schema typing credits as a required int would drop or corrupt them.
    courses = SocParser().parse(sample_payload_bytes).courses
    by_string = {c.courseString: c for c in courses}

    assert by_string["01:013:321"].credits is None
    assert float(by_string["01:050:282"].credits) == 1.5
