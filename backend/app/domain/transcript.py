"""Rutgers unofficial-transcript text -> candidate rows (Phase 7.0).

Pure: text in, rows out. No database, no file, no network, no model. What it
produces is EXTRACTED evidence, never academic truth - every row goes to a
staging table, is normalized against the course table, is reviewed by the
student, and becomes history only on the student's explicit confirmation
(`app.services.transcripts`).

## The layout (checked against ONE real transcript)

A Rutgers New Brunswick unofficial transcript ("UNOFFICIAL COPY OF") is a
server-generated PDF (iText), monospaced, with permissions-only encryption
(an empty user password). Checked against one real document - SAS, four
terms, graded and in-progress courses - the layout is:

```
          TITLE             SCH  DEPT CRS  SUP SEC  CRED  PR GRADE
Fall   2027 SCHOOL OF EXAMPLE STUDIES            <- term heading (+ school)
MAJOR: ...                                       <- term metadata
CLASS: ...
  EXAMPLE COURSE A          98  263  101       01   3.0       A    <- course
    SUB TOPIC: ...                               <- continuation of the course above
  TOTAL CREDITS ATTEMPTED:                               3.0
DEGREE CREDITS EARNED: 3.0   TERM AVG: 4.000  CUMULATIVE AVG: 4.000
...
Last Term Information / LAST TERM ... IN GPA: ...
*** END OF TRANSCRIPT ***
```

(Illustrative, fabricated values - see tests/support/rutgers_layout.py.)
Course codes are COLUMNS (`98 263 101`), not `98:263:101`; the supplement
(SUP) and credit-prefix (PR) columns are usually blank; an in-progress course
has no grade. Page 1 is laid out in TWO COLUMNS (two terms side by side) with
a diagonal watermark. In that document the PDF content stream draws the left
column, then the right, so text extraction follows the terms in order - but
that is a property of the generator, not a guarantee (see "Reading order").

Still unseen in a real document, so still UNVERIFIED: transfer-credit and
exam-credit sections, credit prefixes (E/J/K/N/R), supplement codes, and
repeated courses. Their handling is unchanged and conservative (explicit
section headings only; prefixes recorded, never interpreted).

## Reading order

Two-column pages can be extracted out of order (a different generator, or an
extractor merging the columns by height - reproduced in the tests with a
synthetic interleaved PDF, where pypdf glues the columns into shared lines
such as `... 3.0Spring 2028 SCHOOL ...`). A row assigned to the wrong term
would be a silent academic error, so the parser checks the document against
itself, three ways:

  * MERGED LINES: in a correctly ordered document every structural element
    (term heading, MAJOR, CLASS, a course record, TOTAL CREDITS ATTEMPTED, ...)
    owns its line. A line holding two of them proves columns were merged:
    the document gets `READING_ORDER_UNRELIABLE` and EVERY row is flagged
    (needs review) - no term assignment in it can be trusted;
  * TERM TOTALS: each term block states TOTAL CREDITS ATTEMPTED; the credits
    of the rows read under its heading must add up to it, or every row of
    that block is flagged `TERM_TOTAL_MISMATCH`;
  * NOTHING DROPPED: any course-code triple on a line that does not parse as
    a full record still becomes a row (`COURSE_LINE_UNREADABLE`).

Term headings out of chronological order are a document warning.

## Rules it never breaks

  * Never infers a missing value. No grade -> no grade and no status. No
    term heading above a row -> no term. No credits -> no credits.
  * Never drops a course row. Every course record becomes a row - unreadable
    ones too, with their raw text, so the student sees them and decides.
  * Never decides academic meaning. A "T" grade is a temporary grade, not
    transfer credit; transfer/exam rows are identified only by an explicit
    section heading.
  * Never reads identity. Name, student number and record-date lines are not
    course lines and are ignored; no row carries them.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

#: On every import. Says exactly how far the layout has been checked.
PARSER_VERSION = "rutgers-nb-unofficial/2-checked-against-1-real-transcript"

#: A transcript with more course lines than this is not a transcript.
MAX_ROWS = 400

SEASON_DIGIT = {"WINTER": "0", "SPRING": "1", "SUMMER": "7", "FALL": "9"}

_TERM_HEADER = re.compile(
    r"^\s*(WINTER|SPRING|SUMMER|FALL)\s+(?:SESSION\s+|TERM\s+|SEMESTER\s+)?((?:19|20)\d\d)\b"
    r"\s*(?P<school>.*)$", re.IGNORECASE)
_TRANSFER_HEADER = re.compile(r"^\s*TRANSFER\b", re.IGNORECASE)
_EXAM_HEADER = re.compile(
    r"^\s*(ADVANCED\s+PLACEMENT|AP\s+CREDIT|EXAM(?:INATION)?\s+CREDIT|CREDIT\s+BY\s+EXAM"
    r"|INTERNATIONAL\s+BACCALAUREATE)", re.IGNORECASE)
#: `01:198:112` - the Schedule of Classes rendering of a course code.
_COLON_CODE = re.compile(r"(?<![\d:])(\d{2}):(\d{3}):(\d{3})(?![\d:])")
_CREDITS = re.compile(r"^([EJKNR])?(\d{1,2}\.\d)$")
_CUM_AVG = re.compile(
    r"\bCUM(?:ULATIVE)?\.?\s*(?:GPA|G\.P\.A\.?|AVG|AVERAGE)\s*[:=]?\s*(\d\.\d{1,3})\b",
    re.IGNORECASE)
_TERM_AVG = re.compile(r"\bTERM\s+(?:GPA|AVG|AVERAGE)\s*:?\s*(\d\.\d{1,3})?", re.IGNORECASE)
_ATTEMPTED = re.compile(r"\bTOTAL\s+CREDITS\s+ATTEMPTED\s*:?\s*(\d{1,3}\.\d)?", re.IGNORECASE)
_MAJOR = re.compile(r"^\s*MAJOR\s*:", re.IGNORECASE)
_CLASS = re.compile(r"^\s*CLASS\s*:", re.IGNORECASE)
_SUB_TOPIC = re.compile(r"^\s*SUB\s*TOPIC\s*:", re.IGNORECASE)
_END = re.compile(r"\bEND\s+OF\s+TRANSCRIPT\b", re.IGNORECASE)
#: A course code in the columnar rendering, anywhere on a line.
_CODE_TRIPLE = re.compile(r"(?<![\d.:/])(\d{2}) (\d{3}) (\d{3})(?![\d.:/])")
#: Structural elements that each own a whole line in a correctly ordered
#: document. Two of them on one line (or one beside a course record) means
#: two columns were read as one line.
_STRUCTURE = re.compile(
    r"TOTAL CREDITS ATTEMPTED|DEGREE CREDITS EARNED|MAJOR\s*:|CLASS\s*:|SUB\s*TOPIC\s*:"
    r"|TITLE SCH DEPT|LAST TERM|(?:WINTER|SPRING|SUMMER|FALL)\s+(?:19|20)\d\d\s+SCHOOL",
    re.IGNORECASE)

#: Rutgers' grade vocabulary (NB catalog "Grades and Records"/"Other Grade
#: Symbols"). Used only to RECOGNIZE a token as the grade column; its
#: meaning is app.domain.grades' business.
_LETTERS = ("A", "B+", "B", "C+", "C", "D", "F")
GRADE_TOKENS = frozenset(
    {*_LETTERS, "P", "PA", "NC", "W", "NG", "H", "S", "U", "XF", "TZ",
     *(f"T{letter}" for letter in _LETTERS)})
CREDIT_PREFIXES = frozenset("EJKNR")
#: Looks like a grade column (short, upper-case, +/- allowed) without being
#: one we know - kept as the extracted grade so the student sees it.
_GRADE_LIKE = re.compile(r"^[A-Z]{1,2}[+-]?$")
#: SUP and SEC columns: short alphanumeric codes ("LB", "01", "7E", "H1").
_CODE_TOKEN = re.compile(r"^[A-Z0-9]{1,3}$")


class Section:
    TERM = "term"
    TRANSFER = "transfer"
    EXAM = "exam"
    NONE = "none"


@dataclass(slots=True)
class ExtractedRow:
    """One course record, exactly as read. Values are strings or None - the
    original evidence, before any normalization."""

    row_index: int
    page_number: int
    line_number: int
    raw_text: str
    section: str                    # term | transfer | exam | none (the block kind)
    course_code: str | None         # "01:198:112", from either rendering
    title: str | None
    term_label: str | None          # "FALL 2024", as written
    term_code: str | None           # mechanical mapping of term_label, never a guess
    credits: str | None             # "4.0", as written
    credit_prefix: str | None       # E / J / K / N / R
    grade: str | None               # as written
    class_section: str | None = None    # SEC column ("DB", "7E")
    supplement: str | None = None       # SUP column ("LB"), usually blank
    problems: list[str] = field(default_factory=list)   # parse-level codes
    hints: list[str] = field(default_factory=list)      # informational codes


@dataclass(slots=True)
class TermSummary:
    """A term block's own statements, as written. Evidence for checks and for
    the student's review - never an input to anything CoursePilot computes."""

    term_code: str
    term_label: str
    school: str | None
    credits_attempted: Decimal | None = None
    term_average: Decimal | None = None
    cumulative_average: Decimal | None = None
    #: The rows' credits summed (None if any row's credits are unknown).
    rows_credit_sum: Decimal | None = None
    consistent: bool | None = None      # rows_credit_sum == credits_attempted


@dataclass(slots=True)
class ParsedTranscript:
    parser_version: str
    rows: list[ExtractedRow]
    #: The LAST cumulative average stated on the document, as written.
    #: Evidence the transcript reported, never an input to anything computed.
    reported_cumulative_gpa: Decimal | None
    #: Document-level issue codes (no term headings, too many rows, ...).
    warnings: list[str] = field(default_factory=list)
    terms: list[TermSummary] = field(default_factory=list)
    end_marker_seen: bool = False


def _clean(line: str) -> str:
    # NFKC folds the full-width digits/colons and non-breaking spaces a
    # renderer can emit; whitespace collapses for matching only.
    return " ".join(unicodedata.normalize("NFKC", line).split())


def term_code_for(season: str, year: str) -> str:
    """`FALL`, `2024` -> `20249`. Mechanical: the same mapping
    app.services.planning.terms.label inverts."""
    return f"{year}{SEASON_DIGIT[season.upper()]}"


def _decimal(text: str | None) -> Decimal | None:
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def _columnar_records(tokens: list[str]) -> list[tuple[int, int]]:
    """Every (start, end) token span `SS DDD CCC [SUP] SEC CRED ...` - a course
    record in the columnar rendering. A span starts at a 2-3-3 digit triple
    that is followed, within two code tokens, by a credits token."""
    spans = []
    i = 0
    while i + 3 < len(tokens):
        if (re.fullmatch(r"\d{2}", tokens[i]) and re.fullmatch(r"\d{3}", tokens[i + 1])
                and re.fullmatch(r"\d{3}", tokens[i + 2])):
            for j in range(i + 3, min(i + 6, len(tokens))):
                if _CREDITS.match(tokens[j]):
                    spans.append((i, j))
                    i = j + 1
                    break
                if not _CODE_TOKEN.match(tokens[j]):
                    break
            else:
                i += 1
                continue
            if spans and spans[-1][1] == i - 1:
                continue
        i += 1
    return spans


def _tail(tokens: list[str]) -> tuple[str | None, str | None, list[str]]:
    """PR and GRADE columns after the credits: (prefix, grade, problems).
    A prefix letter (E/J/K/N/R) is never a grade symbol, so one token is
    unambiguous; anything else is kept as written and flagged if odd."""
    problems: list[str] = []
    prefix = grade = None
    if len(tokens) >= 1 and tokens[0] in CREDIT_PREFIXES:
        prefix, tokens = tokens[0], tokens[1:]
    if len(tokens) == 1:
        grade = tokens[0]
        if not (grade in GRADE_TOKENS or _GRADE_LIKE.match(grade)):
            problems.append("GRADE_COLUMN_UNREADABLE")
    elif len(tokens) > 1:
        grade = tokens[0] if (tokens[0] in GRADE_TOKENS or _GRADE_LIKE.match(tokens[0])) \
            else None
        problems.append("UNPARSED_TRAILING_TEXT")
    return prefix, grade, problems


def _parse_columnar(text: str) -> list[dict]:
    tokens = text.split()
    spans = _columnar_records(tokens)
    out = []
    for n, (start, credit_at) in enumerate(spans):
        next_start = spans[n + 1][0] if n + 1 < len(spans) else len(tokens)
        title_start = spans[n - 1][1] + 1 if n else 0
        codes = tokens[start + 3:credit_at]
        supplement, class_section = (codes + [None, None])[:2] if len(codes) == 2 \
            else (None, codes[0] if codes else None)
        m = _CREDITS.match(tokens[credit_at])
        prefix_before, credits = m.group(1), m.group(2)
        tail_tokens = tokens[credit_at + 1:next_start]
        if n + 1 < len(spans):
            # Another record follows on this line: its title is in the tail.
            tail_tokens = [t for t in tail_tokens if t in GRADE_TOKENS or
                           t in CREDIT_PREFIXES][:2]
        prefix, grade, problems = _tail(tail_tokens)
        if len(codes) > 2:
            problems.append("UNPARSED_TRAILING_TEXT")
        if not codes:
            problems.append("SECTION_NOT_FOUND")
        out.append({
            "course_code": f"{tokens[start]}:{tokens[start + 1]}:{tokens[start + 2]}",
            "title": " ".join(tokens[title_start:start]) or None,
            "credits": credits, "credit_prefix": prefix_before or prefix, "grade": grade,
            "class_section": class_section, "supplement": supplement,
            "problems": problems})
    return out


def _parse_colon(text: str, match: re.Match) -> dict:
    """The `01:198:112 TITLE 4.0 A` rendering: credits and grade from the END."""
    rest = text[match.end():].split()
    out = {"course_code": ":".join(match.groups()), "title": None, "credits": None,
           "credit_prefix": None, "grade": None, "class_section": None, "supplement": None,
           "problems": []}
    credit_at = next((i for i in range(len(rest) - 1, -1, -1) if _CREDITS.match(rest[i])),
                     None)
    if credit_at is not None:
        m = _CREDITS.match(rest[credit_at])
        out["credit_prefix"], out["credits"] = m.group(1), m.group(2)
        prefix, grade, problems = _tail(rest[credit_at + 1:])
        out["credit_prefix"] = out["credit_prefix"] or prefix
        out["grade"], out["problems"] = grade, problems
        title_tokens = rest[:credit_at]
    else:
        title_tokens = rest
        if rest and rest[-1] in GRADE_TOKENS:
            out["grade"] = rest[-1]
            title_tokens = rest[:-1]
        out["problems"].append("CREDITS_NOT_FOUND")
    out["title"] = " ".join(title_tokens) or None
    return out


def parse_transcript(pages: list[str]) -> ParsedTranscript:
    """Every course record of `pages` (one string per PDF page), in order."""
    rows: list[ExtractedRow] = []
    warnings: list[str] = []
    terms: list[TermSummary] = []
    block: list[ExtractedRow] = []
    section = Section.NONE
    current: TermSummary | None = None
    reported_gpa: Decimal | None = None
    end_seen = False
    merged_lines = 0

    def close_block():
        if current is None:
            return
        credits = [_decimal(r.credits) for r in block]
        current.rows_credit_sum = None if any(c is None for c in credits) \
            else sum(credits, Decimal("0"))
        if current.credits_attempted is not None and current.rows_credit_sum is not None:
            current.consistent = current.rows_credit_sum == current.credits_attempted
            if not current.consistent:
                for r in block:
                    if "TERM_TOTAL_MISMATCH" not in r.problems:
                        r.problems.append("TERM_TOTAL_MISMATCH")
        if current.term_average is None and current.credits_attempted is not None:
            for r in block:
                if r.grade is None:
                    r.hints.append("TERM_NOT_YET_GRADED")

    for page_number, page in enumerate(pages, start=1):
        for line_number, raw in enumerate(page.splitlines(), start=1):
            text = _clean(raw)
            if not text:
                continue
            if _END.search(text):
                end_seen = True
            for gpa in _CUM_AVG.finditer(text):
                reported_gpa = Decimal(gpa.group(1))
            header = _TERM_HEADER.match(text)
            course_free = not _CODE_TRIPLE.search(text) and not _COLON_CODE.search(text)
            if course_free and len(_STRUCTURE.findall(text)) > 1:
                merged_lines += 1
            if header and not _COLON_CODE.search(text) and not _columnar_records(text.split()):
                close_block()
                block = []
                section = Section.TERM
                code = term_code_for(header.group(1), header.group(2))
                if terms and code < terms[-1].term_code \
                        and "TERM_ORDER_UNEXPECTED" not in warnings:
                    warnings.append("TERM_ORDER_UNEXPECTED")
                current = TermSummary(term_code=code,
                                      term_label=f"{header.group(1).upper()} {header.group(2)}",
                                      school=header.group("school").strip() or None)
                terms.append(current)
                continue
            # A summary line is consumed only when it carries no course: a
            # merged line can hold both, and the course must not be lost.
            if current is not None and section == Section.TERM and course_free:
                attempted = _ATTEMPTED.search(text)
                if attempted:
                    current.credits_attempted = _decimal(attempted.group(1))
                    continue
                avg = _TERM_AVG.search(text)
                if avg:
                    current.term_average = _decimal(avg.group(1))
                    cum = _CUM_AVG.search(text)
                    current.cumulative_average = _decimal(cum.group(1)) if cum else None
                    continue
            if _SUB_TOPIC.match(text) and rows:
                # A variable-topic course's topic: part of the record above.
                rows[-1].raw_text = f"{rows[-1].raw_text} | {text}"[:500]
                continue
            if _MAJOR.match(text) or _CLASS.match(text):
                continue

            colon = list(_COLON_CODE.finditer(text))
            records = [_parse_colon(text, colon[0])] if colon else _parse_columnar(text)
            if not colon:
                # Never drop a course: a code triple the record parser could
                # not read (glued or truncated columns) is still a row.
                parsed_codes = [r["course_code"] for r in records]
                for m in _CODE_TRIPLE.finditer(text):
                    code = ":".join(m.groups())
                    if code in parsed_codes:
                        parsed_codes.remove(code)
                        continue
                    records.append({"course_code": code, "title": None, "credits": None,
                                    "credit_prefix": None, "grade": None,
                                    "class_section": None, "supplement": None,
                                    "problems": ["COURSE_LINE_UNREADABLE"]})
            if records and len(records) + len(_STRUCTURE.findall(text)) > 1:
                merged_lines += 1
            if colon and len(colon) > 1:
                records[0]["problems"].append("MULTIPLE_COURSE_CODES")
            if not records:
                if _TRANSFER_HEADER.match(text):
                    close_block()
                    block, current = [], None
                    section = Section.TRANSFER
                elif _EXAM_HEADER.match(text):
                    close_block()
                    block, current = [], None
                    section = Section.EXAM
                continue
            if len(records) > 1:
                for rec in records:
                    rec["problems"].append("MULTIPLE_COURSE_CODES")

            for rec in records:
                if len(rows) >= MAX_ROWS:
                    warnings.append("TOO_MANY_ROWS")
                    close_block()
                    return ParsedTranscript(PARSER_VERSION, rows, reported_gpa, warnings,
                                            terms, end_seen)
                row = ExtractedRow(
                    row_index=len(rows), page_number=page_number, line_number=line_number,
                    raw_text=text[:500], section=section, course_code=rec["course_code"],
                    title=rec["title"],
                    term_label=current.term_label if current else None,
                    term_code=current.term_code if current else None,
                    credits=rec["credits"], credit_prefix=rec["credit_prefix"],
                    grade=rec["grade"], class_section=rec["class_section"],
                    supplement=rec["supplement"], problems=rec["problems"])
                rows.append(row)
                block.append(row)

    close_block()
    if merged_lines:
        # Columns were read as shared lines: no row's term can be trusted.
        warnings.append("READING_ORDER_UNRELIABLE")
        for r in rows:
            if "READING_ORDER_UNRELIABLE" not in r.problems:
                r.problems.append("READING_ORDER_UNRELIABLE")
    if rows and not terms:
        warnings.append("NO_TERM_HEADINGS")
    if rows and not end_seen:
        # A missing end marker means a missing page is possible (page 1 of a
        # two-page transcript says "CONTINUED ON NEXT PAGE").
        warnings.append("TRANSCRIPT_END_NOT_FOUND")
    if any(t.consistent is False for t in terms):
        warnings.append("TERM_TOTAL_MISMATCH")
    return ParsedTranscript(PARSER_VERSION, rows, reported_gpa, warnings, terms, end_seen)


__all__ = ["CREDIT_PREFIXES", "GRADE_TOKENS", "MAX_ROWS", "PARSER_VERSION", "ExtractedRow",
           "ParsedTranscript", "Section", "TermSummary", "parse_transcript", "term_code_for"]
