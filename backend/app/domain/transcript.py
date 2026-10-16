"""Rutgers unofficial-transcript text -> candidate rows (Phase 7.0).

Pure: text in, rows out. No database, no file, no network, no model. What it
produces is EXTRACTED evidence, never academic truth - every row goes to a
staging table, is normalized against the course table, is reviewed by the
student, and becomes history only on the student's explicit confirmation
(`app.services.transcripts`).

## The layout is UNVERIFIED

A Rutgers New Brunswick unofficial transcript is a my.rutgers web page the
student prints to PDF (Rutgers' own instructions: Academics -> Grades ->
"Full Transcript" -> Print -> Save as PDF). No public sample of its exact
layout exists, and none was available while this was written. So this
parser assumes only what is grounded:

  * course codes are `SS:DDD:CCC` (school:subject:course) - Rutgers' public
    convention, the same codes Schedule of Classes uses;
  * grades come from Rutgers' published grade vocabulary (letters without
    minus, P/NC, W, NG, H, S/U, XF, TZ, T-letter);
  * credit prefixes E/J/K/N/R are Rutgers' published prefixes ("the symbol
    E preceding course credits");
  * terms are named by season and year.

and is deliberately TOLERANT of everything else: it reads lines, anchors on
a course code, and takes credits and grade from the END of the line, so
column order between code and credits (titles, spacing) does not matter.
Synthetic fixtures exercise it; nothing here is claimed to match a real
transcript until a real, redacted sample has been checked. `PARSER_VERSION`
says so on every import.

## Rules it never breaks

  * Never infers a missing value. No grade on the line -> no grade (and
    no guessed status). No term header above a row -> no term. No credits ->
    no credits.
  * Never drops a course row. Every line carrying a course code becomes a
    row - unparseable ones too, with their raw text, so the student sees
    them and decides.
  * Never decides academic meaning. A "T" grade is a temporary grade, not
    transfer credit; transfer/exam rows are identified only by an explicit
    section heading.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

#: On every import. "unverified" is not decoration: see the module docstring.
PARSER_VERSION = "rutgers-nb-unofficial/1-unverified-layout"

#: A transcript with more course lines than this is not a transcript.
MAX_ROWS = 400

SEASON_DIGIT = {"WINTER": "0", "SPRING": "1", "SUMMER": "7", "FALL": "9"}

_TERM_HEADER = re.compile(
    r"^\s*(WINTER|SPRING|SUMMER|FALL)\s+(?:SESSION\s+|TERM\s+|SEMESTER\s+)?((?:19|20)\d\d)\b",
    re.IGNORECASE)
_TRANSFER_HEADER = re.compile(r"^\s*TRANSFER\b", re.IGNORECASE)
_EXAM_HEADER = re.compile(
    r"^\s*(ADVANCED\s+PLACEMENT|AP\s+CREDIT|EXAM(?:INATION)?\s+CREDIT|CREDIT\s+BY\s+EXAM"
    r"|INTERNATIONAL\s+BACCALAUREATE)", re.IGNORECASE)
_COURSE = re.compile(r"(?<![\d:])(\d{2}):(\d{3}):(\d{3})(?![\d:])")
_CREDITS = re.compile(r"^([EJKNR])?(\d{1,2}\.\d)$")
_CUM_GPA = re.compile(
    r"\bCUM(?:ULATIVE)?\.?\s*(?:GPA|G\.P\.A\.?)\s*[:=]?\s*(\d\.\d{1,3})\b", re.IGNORECASE)

#: Rutgers' grade vocabulary (NB catalog "Grades and Records"/"Other Grade
#: Symbols"). Used only to RECOGNIZE a token as the grade column; its
#: meaning is app.domain.grades' business.
_LETTERS = ("A", "B+", "B", "C+", "C", "D", "F")
GRADE_TOKENS = frozenset(
    {*_LETTERS, "P", "PA", "NC", "W", "NG", "H", "S", "U", "XF", "TZ",
     *(f"T{letter}" for letter in _LETTERS)})
#: Looks like a grade column (short, upper-case, +/- allowed) without being
#: one we know - kept as the extracted grade so the student sees it.
_GRADE_LIKE = re.compile(r"^[A-Z]{1,2}[+-]?$")


class Section:
    TERM = "term"
    TRANSFER = "transfer"
    EXAM = "exam"
    NONE = "none"


@dataclass(slots=True)
class ExtractedRow:
    """One course line, exactly as read. Values are strings or None - the
    original evidence, before any normalization."""

    row_index: int
    page_number: int
    line_number: int
    raw_text: str
    section: str
    course_code: str | None
    title: str | None
    term_label: str | None          # "FALL 2024", as written
    term_code: str | None           # mechanical mapping of term_label, never a guess
    credits: str | None             # "4.0", as written
    credit_prefix: str | None       # E / J / K / N / R
    grade: str | None               # as written
    problems: list[str] = field(default_factory=list)   # parse-level issue codes


@dataclass(slots=True)
class ParsedTranscript:
    parser_version: str
    rows: list[ExtractedRow]
    #: The LAST cumulative-GPA line on the document, as written. Evidence the
    #: transcript reported, never an input to anything CoursePilot computes.
    reported_cumulative_gpa: Decimal | None
    #: Document-level issue codes (no term headings, too many rows, ...).
    warnings: list[str] = field(default_factory=list)


def _clean(line: str) -> str:
    # NFKC folds the full-width digits/colons and non-breaking spaces a
    # browser's print-to-PDF can emit; whitespace collapses for matching only.
    return " ".join(unicodedata.normalize("NFKC", line).split())


def term_code_for(season: str, year: str) -> str:
    """`FALL`, `2024` -> `20249`. Mechanical: the same mapping
    app.services.planning.terms.label inverts."""
    return f"{year}{SEASON_DIGIT[season.upper()]}"


def _parse_course_line(text: str, match: re.Match) -> tuple[dict, list[str]]:
    """Title, credits, prefix and grade from the text after the course code.

    Credits and grade are read from the END of the line: a credits token
    (`4.0`, or `E4.0` with a prefix), then at most one grade token. Whatever
    sits between the code and the credits is the title. Anything that does
    not fit is a problem code, never a guess.
    """
    rest = text[match.end():].split()
    out = {"title": None, "credits": None, "credit_prefix": None, "grade": None}
    problems: list[str] = []

    credit_at = None
    for i in range(len(rest) - 1, -1, -1):
        if _CREDITS.match(rest[i]):
            credit_at = i
            break
    if credit_at is not None:
        m = _CREDITS.match(rest[credit_at])
        out["credit_prefix"], out["credits"] = m.group(1), m.group(2)
        tail = rest[credit_at + 1:]
        title_tokens = rest[:credit_at]
        if len(tail) == 1:
            out["grade"] = tail[0]
        elif len(tail) > 1:
            problems.append("UNPARSED_TRAILING_TEXT")
    else:
        title_tokens = rest
        if rest and (rest[-1] in GRADE_TOKENS):
            out["grade"] = rest[-1]
            title_tokens = rest[:-1]
        problems.append("CREDITS_NOT_FOUND")
    if out["grade"] is not None and not (out["grade"] in GRADE_TOKENS
                                         or _GRADE_LIKE.match(out["grade"])):
        problems.append("GRADE_COLUMN_UNREADABLE")
    out["title"] = " ".join(title_tokens) or None
    return out, problems


def parse_transcript(pages: list[str]) -> ParsedTranscript:
    """Every course line of `pages` (one string per PDF page), in order."""
    rows: list[ExtractedRow] = []
    warnings: list[str] = []
    section = Section.NONE
    term_label: str | None = None
    term_code: str | None = None
    saw_term_header = False
    reported_gpa: Decimal | None = None

    for page_number, page in enumerate(pages, start=1):
        for line_number, raw in enumerate(page.splitlines(), start=1):
            text = _clean(raw)
            if not text:
                continue
            gpa = _CUM_GPA.search(text)
            if gpa:
                reported_gpa = Decimal(gpa.group(1))
            header = _TERM_HEADER.match(text)
            codes = list(_COURSE.finditer(text))
            if header and not codes:
                section = Section.TERM
                saw_term_header = True
                term_label = f"{header.group(1).upper()} {header.group(2)}"
                term_code = term_code_for(header.group(1), header.group(2))
                continue
            if not codes:
                if _TRANSFER_HEADER.match(text):
                    section, term_label, term_code = Section.TRANSFER, None, None
                elif _EXAM_HEADER.match(text):
                    section, term_label, term_code = Section.EXAM, None, None
                continue

            if len(rows) >= MAX_ROWS:
                warnings.append("TOO_MANY_ROWS")
                return ParsedTranscript(PARSER_VERSION, rows, reported_gpa, warnings)

            first = codes[0]
            parsed, problems = _parse_course_line(text, first)
            if len(codes) > 1:
                problems.append("MULTIPLE_COURSE_CODES")
            rows.append(ExtractedRow(
                row_index=len(rows), page_number=page_number, line_number=line_number,
                raw_text=text[:500], section=section,
                course_code=":".join(first.groups()), title=parsed["title"],
                term_label=term_label, term_code=term_code, credits=parsed["credits"],
                credit_prefix=parsed["credit_prefix"], grade=parsed["grade"],
                problems=problems))

    if rows and not saw_term_header:
        warnings.append("NO_TERM_HEADINGS")
    return ParsedTranscript(PARSER_VERSION, rows, reported_gpa, warnings)


__all__ = ["GRADE_TOKENS", "MAX_ROWS", "PARSER_VERSION", "ExtractedRow",
           "ParsedTranscript", "Section", "parse_transcript", "term_code_for"]
