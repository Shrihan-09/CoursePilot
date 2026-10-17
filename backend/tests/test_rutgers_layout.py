"""The REAL Rutgers unofficial-transcript layout, on a SANITIZED fixture (Phase 7.0).

The parser was checked against one real Rutgers NB transcript (local only;
never committed). These tests pin what that document taught, using
`tests/support/rutgers_layout.py`: the same STRUCTURE, entirely fabricated
CONTENT (identity, student number, courses, sections, grades, averages).

No database. The extractor tests run the real pypdf extractor in its child
process.
"""

from __future__ import annotations

import io
from decimal import Decimal

import pytest

from app.domain.transcript import parse_transcript
from app.services.transcripts.extract import ExtractionFailed, isolated_pdf_extractor

from .support.rutgers_layout import (
    FAKE_NAME,
    FAKE_STUDENT_NUMBER,
    build_rutgers_pdf,
    standard_fixture,
    term_block,
)

CODES = {k: f"98:263:{101 + i}" for i, k in enumerate("ABCDE")}


def _pages(pdf: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf))
    if reader.is_encrypted:
        assert reader.decrypt("")
    return [p.extract_text() for p in reader.pages]


def _rows(parsed):
    return [(r.term_code, r.course_code, r.class_section, r.credits, r.grade)
            for r in parsed.rows]


@pytest.fixture(scope="module")
def fx():
    return standard_fixture(CODES)


def test_the_real_layout_is_read_column_by_column(fx) -> None:
    parsed = parse_transcript(_pages(build_rutgers_pdf(fx["left"], fx["right"], fx["page2"])))
    assert _rows(parsed) == fx["expected"]
    assert parsed.warnings == [] and parsed.end_marker_seen
    assert parsed.reported_cumulative_gpa == Decimal(fx["reported_cumulative"])
    assert [(t.term_code, t.consistent) for t in parsed.terms] == [
        ("20279", True), ("20281", True), ("20289", True), ("20291", True)]
    by_term = {t.term_code: t for t in parsed.terms}
    assert by_term["20279"].term_average == Decimal("3.429")
    assert by_term["20291"].term_average is None and by_term["20291"].credits_attempted == 7
    assert all(t.school == "SCHOOL OF EXAMPLE STUDIES" for t in parsed.terms)


def test_an_ungraded_term_is_hinted_never_guessed(fx) -> None:
    parsed = parse_transcript(_pages(build_rutgers_pdf(fx["left"], fx["right"], fx["page2"])))
    ungraded = [r for r in parsed.rows if r.term_code == "20291"]
    assert ungraded and all(r.grade is None and r.hints == ["TERM_NOT_YET_GRADED"]
                            for r in ungraded)
    assert all(not r.hints for r in parsed.rows if r.term_code != "20291")


def test_a_failed_course_and_its_retake_are_two_attempts(fx) -> None:
    parsed = parse_transcript(_pages(build_rutgers_pdf(fx["left"], fx["right"], fx["page2"])))
    attempts = [(r.term_code, r.grade) for r in parsed.rows if r.course_code == CODES["C"]]
    assert attempts == [("20281", "F"), ("20289", "B")]


def test_the_sub_topic_line_stays_with_its_course(fx) -> None:
    parsed = parse_transcript(_pages(build_rutgers_pdf(fx["left"], fx["right"], fx["page2"])))
    first = parsed.rows[0]
    assert first.course_code == CODES["A"] and "SUB TOPIC: EXAMPLE TOPIC" in first.raw_text
    assert len(parsed.rows) == len(fx["expected"])      # not a row of its own


def test_identity_lines_and_the_watermark_never_reach_a_row(fx) -> None:
    parsed = parse_transcript(_pages(build_rutgers_pdf(fx["left"], fx["right"], fx["page2"])))
    blob = " ".join(f"{r.raw_text} {r.title}" for r in parsed.rows)
    for token in (*FAKE_NAME.split(), FAKE_STUDENT_NUMBER, "UNOFFICIAL", "RECORD"):
        assert token not in blob, token


def test_interleaved_columns_flag_every_row_and_drop_none(fx) -> None:
    """The highest-risk defect: a two-column page read line by line across
    both columns. pypdf then glues the columns into shared lines. Nothing may
    be dropped and no term assignment may be trusted silently."""
    parsed = parse_transcript(_pages(build_rutgers_pdf(fx["left"], fx["right"], fx["page2"],
                                                       order="interleaved")))
    assert "READING_ORDER_UNRELIABLE" in parsed.warnings
    assert sorted(r.course_code for r in parsed.rows) == sorted(e[1] for e in fx["expected"])
    assert all("READING_ORDER_UNRELIABLE" in r.problems for r in parsed.rows)


def test_rows_that_do_not_add_up_to_their_term_total_are_flagged() -> None:
    left = (term_block("Fall", 2027, [("EXAMPLE COURSE A", CODES["A"], "01", "3.0", "A"),
                                      ("EXAMPLE COURSE B", CODES["B"], "02", "4.0", "B")],
                       term_avg="3.429", cum_avg="3.429", attempted="10.0")
            + term_block("Spring", 2028, [("EXAMPLE COURSE C", CODES["C"], "03", "3.0", "A")],
                         term_avg="4.000", cum_avg="3.600"))
    parsed = parse_transcript(_pages(build_rutgers_pdf(left, [], [])))
    flagged = {r.course_code: "TERM_TOTAL_MISMATCH" in r.problems for r in parsed.rows}
    assert flagged == {CODES["A"]: True, CODES["B"]: True, CODES["C"]: False}
    assert "TERM_TOTAL_MISMATCH" in parsed.warnings


def test_a_missing_last_page_is_warned(fx) -> None:
    parsed = parse_transcript(_pages(build_rutgers_pdf(fx["left"], fx["right"], [],
                                                       end_marker=False)))
    assert "TRANSCRIPT_END_NOT_FOUND" in parsed.warnings
    assert _rows(parsed) == fx["expected"]


def test_supplement_and_credit_prefix_columns() -> None:
    """Not yet seen on a real transcript (the sample had none): the columns are
    read positionally after the course code and credits, as the header orders
    them - SUP before SEC, PR before GRADE."""
    rows = [("EXAMPLE LAB", CODES["A"], "01", "1.0", "A", "LB"),
            ("EXAMPLE REPEAT", CODES["B"], "02", "3.0", "B", "", "E")]
    left = term_block("Fall", 2027, rows, term_avg="3.500", cum_avg="3.500")
    parsed = parse_transcript(_pages(build_rutgers_pdf(left, [], [])))
    lab, repeat = parsed.rows
    assert (lab.supplement, lab.class_section, lab.credits, lab.grade) == ("LB", "01", "1.0", "A")
    assert (repeat.credit_prefix, repeat.grade, repeat.supplement) == ("E", "B", None)


@pytest.fixture(scope="module")
def extractor():
    return isolated_pdf_extractor(max_pages=20, max_chars=200_000, timeout_seconds=60)


def test_permissions_only_encryption_is_opened_by_the_real_extractor(fx, extractor) -> None:
    """Real Rutgers transcripts are 'encrypted' with an EMPTY user password.
    Refusing them refused every real transcript (found on the real sample)."""
    out = extractor(build_rutgers_pdf(fx["left"], fx["right"], fx["page2"]))
    assert out.page_count == 2
    assert _rows(parse_transcript(out.pages)) == fx["expected"]


def test_a_document_needing_a_real_password_is_still_refused(fx, extractor) -> None:
    with pytest.raises(ExtractionFailed) as exc:
        extractor(build_rutgers_pdf(fx["left"], fx["right"], fx["page2"],
                                    user_password="not-empty"))
    assert exc.value.code == "PDF_ENCRYPTED"
