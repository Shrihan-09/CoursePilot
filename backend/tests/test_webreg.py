"""Sanitized positioned PDF fixtures and fail-closed matching evidence."""

from __future__ import annotations

import pytest

from app.domain.webreg import ScheduleParseError, parse_schedule
from app.services.scheduling.pdf_worker import _layout_child, extract_layout
from app.services.transcripts.extract import ExtractionFailed, isolated_pdf_extractor

from .support.synthetic_webreg import build_schedule_pdf, extracted_layout, objects


def parse(items):
    return parse_schedule(["\n".join(item[2] for item in items)], extracted_layout(items))


def test_repeated_tuesday_friday_and_monday_wednesday_deduplicate():
    items = objects(
        records=[
            (170, 610, "01:999:101:01-91234 (4.0)"),
            (470, 580, "01:999:101:01-91234 (4.0)"),
            (70, 540, "01:999:102:02-91235 (3.0)"),
            (270, 510, "01:999:102:02-91235 (3.0)"),
        ]
    )
    rows = parse(items)
    assert len(rows) == 2 and rows[0]["term_code"] == "20999"
    assert all("day" not in row and "meetings" not in row for row in rows)


def test_evening_single_multiple_and_arrangement_sections():
    rows = parse(
        objects(
            records=[
                (70, 610, "01:999:101:01-91234 (4.0)"),
                (170, 410, "01:999:102:02-91235 (3.0)"),
            ],
            arrangement=[
                "* INVENTED ASTRONOMY (01:999:103:03) 91236",
                "* INVENTED ART (01:999:104:04) 91237",
            ],
        )
    )
    assert len(rows) == 4 and rows[-1]["index_number"] == "91237"
    assert all("modality" not in row for row in rows)  # SOC, not room absence, supplies modality


def test_grid_identity_with_colon_before_index_is_read():
    """Phase 7.2.2 real-format finding: a browser-saved WebReg print joins the
    index to the section with a COLON (course:section:index), not a hyphen."""
    rows = parse(
        objects(
            records=[
                (170, 610, "01:999:101:01:91234"),
                (470, 580, "01:999:101:01:91234"),
                (270, 540, "01:999:102:02:91235"),
            ]
        )
    )
    assert [(r["course_string"], r["section_number"], r["index_number"]) for r in rows] == [
        ("01:999:101", "01", "91234"),
        ("01:999:102", "02", "91235"),
    ]


def test_colon_form_through_real_pdf_extraction_with_arrangement_rows():
    pdf = build_schedule_pdf(
        objects(
            records=[(170, 610, "01:999:101:01:91234"), (370, 560, "01:999:102:02:91235")],
            arrangement=["* INVENTED ASYNC COURSE (01:999:103:90) 91236"],
        )
    )
    result = extract_layout(pdf, 20, 200000)
    rows = parse_schedule(result[1], result[3])
    assert [r["index_number"] for r in rows] == ["91234", "91235", "91236"]


@pytest.mark.parametrize(
    "text",
    [
        "01:999:101:01:9123",  # four-digit index
        "01:999:101:01:912345",  # six-digit index
        "01:999:101:01::91234",  # doubled separator
        "01:999:101:01:91234 01:999:102:02:91235",  # two identities on one line
    ],
)
def test_colon_form_stays_exact(text):
    with pytest.raises(ScheduleParseError, match="UNSUPPORTED_LAYOUT"):
        parse(objects(records=[(170, 610, text)]))


def test_fallback_requires_exact_arrangement_identity():
    rows = parse(objects(records=[], arrangement=["* INVENTED COURSE (01:999:103:03)"]))
    assert rows[0]["index_number"] is None


def test_same_baseline_objects_are_joined_within_one_grid_column():
    items = objects(records=[(70, 600, "01:999:101:01-"), (130, 600, "91234 (4.0)")])
    # A split identifier is not repaired by concatenating arbitrary text runs.
    with pytest.raises(ScheduleParseError, match="UNSUPPORTED_LAYOUT"):
        parse(items)


@pytest.mark.parametrize(
    "items,code",
    [
        (objects(term="No term supplied"), "SCHEDULE_TERM_AMBIGUOUS"),
        (objects() + [(40, 730, "Spring 2099")], "SCHEDULE_TERM_AMBIGUOUS"),
        (
            objects(records=[(170, 610, "01:999:101:01-91234"), (470, 580, "01:999:101:01-99999")]),
            "SCHEDULE_IDENTIFIERS_CONFLICT",
        ),
        (objects(records=[(170, 610, "01:999:101:01 91234")]), "UNSUPPORTED_LAYOUT"),
        (objects(records=[(170, 610, "01:999:101:01-123456")]), "UNSUPPORTED_LAYOUT"),
        (
            objects(records=[(170, 610, "01:999:101:01-91234 01:999:102:02-91235")]),
            "UNSUPPORTED_LAYOUT",
        ),
        (objects(records=[(40, 750, "01:999:101:01-91234")]), "UNSUPPORTED_LAYOUT"),
    ],
)
def test_ambiguous_missing_or_unsupported_structure_refused(items, code):
    with pytest.raises(ScheduleParseError, match=code):
        parse(items)


@pytest.mark.parametrize(
    "items",
    [
        # An identity outside every recognised region beside a valid row.
        objects(records=[(170, 610, "01:999:101:01-91234")]) + [(40, 750, "01:999:102:02-91235")],
        # One exact grid identity plus a second course reference on the same line.
        objects(records=[(170, 610, "01:999:101:01-91234 (01:999:102)")]),
    ],
)
def test_unexplained_course_text_is_refused_not_dropped(items):
    with pytest.raises(ScheduleParseError, match="UNSUPPORTED_LAYOUT"):
        parse(items)


def test_plain_sequential_text_without_layout_is_not_accepted():
    with pytest.raises(ScheduleParseError, match="UNSUPPORTED_LAYOUT"):
        parse_schedule(["WebReg | View / Print Schedule\nFall 2099\n01:999:101:01-91234"])


def test_fabricated_positioned_pdf_runs_real_extraction():
    result = extract_layout(build_schedule_pdf(), 20, 200000)
    assert result[0] == "ok"
    assert len(parse_schedule(result[1], result[3])) == 1
    assert result[3][0]["fragments"][0]["x"] == 40


def test_raster_only_is_unsupported_no_ocr():
    assert extract_layout(build_schedule_pdf(raster_only=True), 20, 200000) == (
        "error",
        "UNSUPPORTED_LAYOUT",
    )


def test_glyphs_drawn_as_outlines_have_no_text_layer_and_fail_closed():
    """Phase 7.2.2 real-format finding: a schedule printed through a driver that
    converts text to vector shapes (e.g. "Microsoft Print To PDF") carries no
    character data. Reading it would be OCR, which is deliberately not done."""
    import io

    from pypdf import PdfReader

    pdf = build_schedule_pdf(outlined=True)
    page = PdfReader(io.BytesIO(pdf)).pages[0]
    raw = b"".join(s.get_object().get_data() for s in page["/Contents"])
    # The fixture really has the discovered characteristics.
    assert page["/Resources"] == {} and len(page["/Contents"]) == 2
    assert b" c " in raw and b"BT" not in raw and b"Tj" not in raw
    assert extract_layout(pdf, 20, 200000) == ("error", "UNSUPPORTED_LAYOUT")
    # The same items as a text layer parse, so the refusal is the representation.
    assert extract_layout(build_schedule_pdf(), 20, 200000)[0] == "ok"


def test_layout_extraction_isolated_process():
    extractor = isolated_pdf_extractor(
        max_pages=20, max_chars=200000, timeout_seconds=15, _target=_layout_child
    )
    result = extractor(build_schedule_pdf())
    assert len(parse_schedule(result.pages, result.layout)) == 1
    with pytest.raises(ExtractionFailed, match="UNSUPPORTED_LAYOUT"):
        extractor(build_schedule_pdf(raster_only=True))


def test_committed_synthetic_webreg_fixture_matches_the_real_layout_kind():
    """Phase 7.3: the committed fixture is SYNTHETIC (invented data) but copies
    the real browser-saved print layout, element for element."""
    import pathlib

    from .support.synthetic_webreg import realistic_objects

    fixture = (pathlib.Path(__file__).parent / "fixtures" / "synthetic"
               / "webreg_schedule.SYNTHETIC.pdf")
    assert fixture.read_bytes() == build_schedule_pdf(realistic_objects())   # regenerable
    result = extract_layout(fixture.read_bytes(), 20, 200000)
    rows = parse_schedule(result[1], result[3])
    assert [(r["course_string"], r["section_number"], r["index_number"]) for r in rows] == [
        ("01:999:101", "01", "91234"), ("01:999:102", "02", "91235"),
        ("01:999:103", "03", "91236"), ("01:999:150", "90", "91250")]
