"""Phase 7.0 pure layers: entry validation, transcript parsing, PDF extraction.

No database. The documents are SYNTHETIC (`tests/support/synthetic_transcript.py`);
the extractor tests run the REAL pypdf extractor in its child process.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.history import IssueSeverity, validate_entry
from app.domain.transcript import MAX_ROWS, PARSER_VERSION, Section, parse_transcript
from app.services.transcripts.extract import (
    ExtractionFailed,
    check_document,
    isolated_pdf_extractor,
)
from app.services.transcripts.storage import (
    LocalTranscriptStorage,
    MemoryTranscriptStorage,
    StorageError,
)

from .support.synthetic_transcript import build_pdf, transcript_lines


def _codes(entry):
    return {i.code: i.severity for i in entry.issues}


# --------------------------------------------------------------------------
# validate_entry
# --------------------------------------------------------------------------


def test_a_completed_rutgers_course_needs_a_real_grade() -> None:
    ok = validate_entry(term_code="20249", status="completed", grade=" b+ ",
                        credits_earned="4", credit_origin="rutgers")
    assert not ok.issues and ok.grade == "B+" and ok.credits_earned == Decimal("4.0")
    missing = validate_entry(term_code="20249", status="completed", grade=None,
                             credits_earned=None, credit_origin="rutgers")
    assert _codes(missing) == {"GRADE_MISSING": IssueSeverity.INVALID}
    # No minus grades at Rutgers: never stored, never rounded to a neighbour.
    minus = validate_entry(term_code="20249", status="completed", grade="A-",
                           credits_earned=None, credit_origin="rutgers")
    assert _codes(minus) == {"GRADE_UNRECOGNIZED": IssueSeverity.INVALID}
    for symbol in ("A", "C", "F", "P", "NC", "W", "NG", "H", "S", "U", "XF", "TZ", "TB+"):
        assert not validate_entry(term_code="20249", status="completed", grade=symbol,
                                  credits_earned=None, credit_origin=None).issues, symbol


def test_planned_in_progress_and_external_credit_carry_no_grade() -> None:
    for status in ("planned", "in_progress"):
        e = validate_entry(term_code="20269", status=status, grade="A", credits_earned=None,
                           credit_origin="rutgers")
        assert _codes(e) == {"GRADE_NOT_ALLOWED": IssueSeverity.INVALID}
    for origin in ("transfer", "exam_credit"):
        # A transfer grade is not a Rutgers grade; storing it would fabricate one.
        graded = validate_entry(term_code="20249", status="completed", grade="A",
                                credits_earned="3", credit_origin=origin)
        assert _codes(graded) == {"GRADE_NOT_ALLOWED": IssueSeverity.INVALID}
        planned = validate_entry(term_code="20249", status="planned", grade=None,
                                 credits_earned="3", credit_origin=origin)
        assert "EXTERNAL_CREDIT_NOT_COMPLETED" in _codes(planned)
        ok = validate_entry(term_code="20249", status="completed", grade=None,
                            credits_earned="3", credit_origin=origin)
        assert not ok.issues and ok.grade is None


def test_terms_statuses_origins_and_credits_are_checked() -> None:
    for bad in ("2024", "20242", "Fall 2024", "2024-9", "202490"):
        e = validate_entry(term_code=bad, status="planned", grade=None, credits_earned=None,
                           credit_origin=None)
        assert _codes(e) == {"TERM_INVALID": IssueSeverity.INVALID}, bad
    assert "STATUS_INVALID" in _codes(validate_entry(
        term_code="20249", status="dropped", grade=None, credits_earned=None,
        credit_origin=None))
    assert "CREDIT_ORIGIN_INVALID" in _codes(validate_entry(
        term_code="20249", status="planned", grade=None, credits_earned=None,
        credit_origin="clep"))
    for bad in ("-1", "1000", "3.25", "abc", "nan", "inf"):
        e = validate_entry(term_code="20249", status="planned", grade=None,
                           credits_earned=bad, credit_origin=None)
        assert _codes(e) == {"CREDITS_INVALID": IssueSeverity.INVALID}, bad


def test_a_missing_value_from_a_document_needs_review_not_a_guess() -> None:
    e = validate_entry(term_code=None, status=None, grade=None, credits_earned=None,
                       credit_origin="rutgers", missing_severity=IssueSeverity.NEEDS_REVIEW)
    assert _codes(e) == {"TERM_MISSING": IssueSeverity.NEEDS_REVIEW,
                         "STATUS_MISSING": IssueSeverity.NEEDS_REVIEW}
    assert e.status is None and e.term_code is None


# --------------------------------------------------------------------------
# parse_transcript
# --------------------------------------------------------------------------


def _page(**kw):
    return "\n".join(transcript_lines(**kw))


def test_terms_courses_credits_grades_and_reported_gpa_are_read() -> None:
    parsed = parse_transcript([_page(courses={
        "FALL 2024": ["01:198:111 INTRO COMPUTER SCI 4.0 A",
                      "01:640:151 CALC MATH/PHYS I E4.0 B+"],
        "SPRING 2025": ["01:198:112 DATA STRUCTURES 4.0"]})])
    assert parsed.parser_version == PARSER_VERSION and "unverified" in PARSER_VERSION
    got = [(r.course_code, r.title, r.term_code, r.term_label, r.credits, r.credit_prefix,
            r.grade, r.section) for r in parsed.rows]
    assert got == [
        ("01:198:111", "INTRO COMPUTER SCI", "20249", "FALL 2024", "4.0", None, "A", "term"),
        ("01:640:151", "CALC MATH/PHYS I", "20249", "FALL 2024", "4.0", "E", "B+", "term"),
        # No grade on the line: none is invented, and neither is a status.
        ("01:198:112", "DATA STRUCTURES", "20251", "SPRING 2025", "4.0", None, None, "term")]
    assert parsed.reported_cumulative_gpa == Decimal("3.412")
    assert [(r.page_number, r.row_index) for r in parsed.rows] == [(1, 0), (1, 1), (1, 2)]


def test_transfer_and_exam_sections_are_explicit_never_inferred_from_a_t_grade() -> None:
    parsed = parse_transcript([_page(
        courses={"FALL 2024": ["01:198:111 INTRO COMPUTER SCI 4.0 TB"]},
        transfer=["01:355:101 COLLEGE WRITING 3.0 TA"],
        exam=["01:640:135 CALCULUS I 4.0"])])
    sections = {r.course_code: (r.section, r.term_code) for r in parsed.rows}
    assert sections == {"01:355:101": (Section.TRANSFER, None),
                        "01:640:135": (Section.EXAM, None),
                        # TB is a temporary grade in a term, NOT transfer credit.
                        "01:198:111": (Section.TERM, "20249")}


def test_every_course_line_is_kept_even_when_unreadable() -> None:
    parsed = parse_transcript(["FALL 2024\n"
                               "01:750:203 PHYS 01:750:204 4.0 A\n"
                               "01:198:205 DISCRETE STRUCT 4.0 A- \n"
                               "01:198:206 STATS 4.0 A B C\n"
                               "01:198:211 COMPUTER ARCH\n"
                               "random footer line with no course\n"])
    by = {r.course_code: r for r in parsed.rows}
    assert len(parsed.rows) == 4       # the footer is not a course row; no course row dropped
    assert by["01:750:203"].problems == ["MULTIPLE_COURSE_CODES"]
    assert by["01:198:205"].grade == "A-" and by["01:198:205"].problems == []
    assert by["01:198:206"].problems == ["UNPARSED_TRAILING_TEXT"]
    assert by["01:198:211"].credits is None and "CREDITS_NOT_FOUND" in by["01:198:211"].problems
    assert all(r.raw_text for r in parsed.rows)


def test_no_term_heading_is_reported_and_rows_have_no_term() -> None:
    parsed = parse_transcript(["01:198:111 INTRO COMPUTER SCI 4.0 A"])
    assert parsed.warnings == ["NO_TERM_HEADINGS"] and parsed.rows[0].term_code is None


def test_row_count_is_bounded() -> None:
    lines = "FALL 2024\n" + "\n".join(f"01:198:{100 + i % 800:03d} X 3.0 A"
                                      for i in range(MAX_ROWS + 5))
    parsed = parse_transcript([lines])
    assert "TOO_MANY_ROWS" in parsed.warnings and len(parsed.rows) == MAX_ROWS


def test_unicode_variants_from_print_to_pdf_are_folded() -> None:
    # Full-width digits/colon and a non-breaking space, as a browser can emit.
    parsed = parse_transcript(["FALL 2024\n０１:198:111 INTRO 4.0 A"])
    assert [(r.course_code, r.term_code) for r in parsed.rows] == [("01:198:111", "20249")]


# --------------------------------------------------------------------------
# document checks and the isolated extractor
# --------------------------------------------------------------------------


def test_type_is_decided_by_content_not_by_name_or_header() -> None:
    check_document(build_pdf([["x"]]), 10_000)
    for data, code in ((b"", "FILE_EMPTY"), (b"\x89PNG\r\n\x1a\n" + b"0" * 64, "FILE_NOT_PDF"),
                       (b"PK\x03\x04" + b"0" * 64, "FILE_NOT_PDF"),
                       (b"x" * 2048 + b"%PDF-1.4", "FILE_NOT_PDF"),
                       (build_pdf([["x"]]), None)):
        if code is None:
            with pytest.raises(ExtractionFailed) as exc:
                check_document(data, 10)
            assert exc.value.code == "FILE_TOO_LARGE"
            continue
        with pytest.raises(ExtractionFailed) as exc:
            check_document(data, 10_000)
        assert exc.value.code == code


@pytest.fixture(scope="module")
def extractor():
    return isolated_pdf_extractor(max_pages=3, max_chars=5_000, timeout_seconds=60)


def test_the_real_extractor_reads_a_text_pdf_in_a_child_process(extractor) -> None:
    out = extractor(build_pdf([transcript_lines(
        courses={"FALL 2024": ["01:198:111 INTRO COMPUTER SCI 4.0 A"]})]))
    assert out.page_count == 1
    assert [r.course_code for r in parse_transcript(out.pages).rows] == ["01:198:111"]


@pytest.mark.parametrize("data,code", [
    (build_pdf([["page"]] * 4), "PDF_TOO_MANY_PAGES"),
    (build_pdf([["x" * 90] * 70]), "PDF_TEXT_TOO_LARGE"),
    (build_pdf([[]]), "PDF_NO_TEXT_LAYER"),
    (build_pdf([["secret"]], encrypt_marker=True), "PDF_ENCRYPTED"),
    (b"%PDF-1.4\n" + b"\x00garbage" * 50, "PDF_UNREADABLE"),
])
def test_hostile_or_unusable_pdfs_fail_with_a_fixed_code(extractor, data, code) -> None:
    with pytest.raises(ExtractionFailed) as exc:
        extractor(data)
    assert exc.value.code == code


def _hanging_parser(conn, data, max_pages, max_chars):   # runs in the child
    import time

    time.sleep(120)


def _crashing_parser(conn, data, max_pages, max_chars):  # runs in the child
    import os

    os._exit(3)


def test_a_hung_parser_is_killed_at_the_timeout() -> None:
    import time

    hung = isolated_pdf_extractor(max_pages=3, max_chars=5_000, timeout_seconds=8,
                                  _target=_hanging_parser)
    started = time.monotonic()
    with pytest.raises(ExtractionFailed) as exc:
        hung(build_pdf([["x"]]))
    assert exc.value.code == "PDF_EXTRACTION_TIMEOUT"
    assert time.monotonic() - started < 30


def test_a_crashing_parser_is_contained() -> None:
    crashing = isolated_pdf_extractor(max_pages=3, max_chars=5_000, timeout_seconds=60,
                                      _target=_crashing_parser)
    with pytest.raises(ExtractionFailed) as exc:
        crashing(build_pdf([["x"]]))
    assert exc.value.code == "PDF_EXTRACTION_CRASHED"


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------


def test_local_storage_uses_generated_keys_and_never_escapes(tmp_path) -> None:
    store = LocalTranscriptStorage(tmp_path / "t")
    key = store.put(b"%PDF-1.4 x")
    assert len(key) == 32 and store.get(key) == b"%PDF-1.4 x" and store.stored_keys() == [key]
    for evil in ("../../etc/passwd", "..\\..\\x", "a" * 31 + "/", "A" * 32, "x" * 32):
        with pytest.raises(StorageError):
            store.get(evil)
    store.delete(key)
    store.delete(key)            # idempotent
    assert store.stored_keys() == [] and list((tmp_path / "t").iterdir()) == []
    mem = MemoryTranscriptStorage()
    k = mem.put(b"x")
    mem.delete(k)
    assert mem.stored_keys() == []
