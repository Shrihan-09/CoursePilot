"""Normalizer tests: RawSocCourse -> NormalizedCourse."""

from __future__ import annotations

from decimal import Decimal

from coursepilot_ingestion.normalizers.soc import SocNormalizer, _clean_text
from coursepilot_ingestion.parsers.soc import SocParser

TERM = "20269"


def _normalized(payload: bytes) -> dict:
    courses = SocParser().parse(payload).courses
    n = SocNormalizer(term_code=TERM)
    out = {}
    for raw in courses:
        norm = n.normalize(raw)
        out.setdefault(raw.courseString, []).append(norm)
    return out


def test_prefers_expanded_title_but_keeps_abbreviation(sample_payload_bytes: bytes) -> None:
    # SOC `title` is truncated to ~20 chars; `expandedTitle` has the real one
    # but is present on only 60.8% of records.
    course = _normalized(sample_payload_bytes)["01:013:130"][0]

    assert course.title == "COMICS IN THE MIDDLE EAST"
    assert course.title_abbrev == "COMICS MIDEAST"


def test_falls_back_to_title_when_no_expanded_title(sample_payload_bytes: bytes) -> None:
    for group in _normalized(sample_payload_bytes).values():
        for course in group:
            assert course.title, "every course must end up with some title"


def test_supplement_code_whitespace_becomes_empty_string(sample_payload_bytes: bytes) -> None:
    # SOC sends '  ' for "no supplement". It must normalize to '' and never
    # to NULL: the column is part of a UNIQUE constraint, and NULL != NULL in
    # SQL would let Postgres accept unlimited duplicates.
    lecture, lab = _normalized(sample_payload_bytes)["01:750:193"]
    codes = {lecture.supplement_code, lab.supplement_code}

    assert codes == {"", "LB"}
    assert None not in codes


def test_strips_html_from_prerequisite_prose(sample_payload_bytes: bytes) -> None:
    # Real value contains "<em> OR </em>" markup.
    course = _normalized(sample_payload_bytes)["01:013:240"][0]

    assert course.prereq_notes_raw is not None
    assert "<em>" not in course.prereq_notes_raw
    assert "OR" in course.prereq_notes_raw
    # The course references embedded in the prose must survive cleaning.
    assert "01:013:141" in course.prereq_notes_raw


def test_empty_description_becomes_none_not_empty_string(sample_payload_bytes: bytes) -> None:
    # SOC returns courseDescription empty for 100% of records. It must land as
    # NULL so "we have no description" is distinguishable from "the
    # description is blank", and so a catalog source can fill it later.
    for group in _normalized(sample_payload_bytes).values():
        for course in group:
            assert course.description is None


def test_does_not_invent_a_description_from_the_title(sample_payload_bytes: bytes) -> None:
    # Guards against a tempting "helpful" default that would fabricate content
    # the source never provided.
    for group in _normalized(sample_payload_bytes).values():
        for course in group:
            assert course.description != course.title


def test_campus_distinguishes_the_duplicate_pair(sample_payload_bytes: bytes) -> None:
    # 16:400:513 appears twice, differing only by campus.
    nb, ob = _normalized(sample_payload_bytes)["16:400:513"]

    assert {nb.campus_code, ob.campus_code} == {"NB", "OB"}
    # Same course identity despite two records: campus is NOT part of the key.
    assert nb.natural_key == ob.natural_key


def test_supplement_distinguishes_lecture_from_lab(sample_payload_bytes: bytes) -> None:
    lecture, lab = _normalized(sample_payload_bytes)["01:750:193"]

    # Different identity: these are genuinely different courses.
    assert lecture.natural_key != lab.natural_key
    assert {lecture.credits, lab.credits} == {Decimal(4), Decimal(0)}


def test_credits_preserved_exactly(sample_payload_bytes: bytes) -> None:
    data = _normalized(sample_payload_bytes)

    assert data["01:013:321"][0].credits is None
    assert data["01:050:282"][0].credits == Decimal("1.5")


def test_clean_text_handles_none_and_blank() -> None:
    assert _clean_text(None) is None
    assert _clean_text("") is None
    assert _clean_text("   ") is None
    assert _clean_text("<b>hi</b>  there") == "hi there"
    assert _clean_text("A &amp; B") == "A & B"
