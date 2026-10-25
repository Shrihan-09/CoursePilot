"""Parse a Degree Navigator audit PDF's positioned layout (Phase 7.4).

Input: what `app.services.degree_audit.pdf_worker` reports per page - text
fragments and drawn images in user space. Output: degree blocks, each with its
requirement headings, DN status ICONS (classified against DN's own legend in
the same document), Core goal sets, and every course row - all as EVIDENCE.
Nothing here decides academic meaning; `app.services.degree_audit` stages it
for the student's review.

Layout facts this relies on (measured on a real Chrome "Save as PDF" audit;
docs/investigations/phase-7-3-csp-dn.md section 4):

* Each label and value is its own fragment; a line is the fragments sharing a
  baseline, in x order. Numbered course lists put two courses on one line, so
  a line is split on its "n." items, never read as one record.
* Status is an image: a requirement's icon sits on its heading's baseline left
  of the text; a course's icon sits right after the course code in the rule
  text. DN's legend draws each icon next to its meaning; an icon is classified
  ONLY by being pixel-identical (SHA-256 of decoded data) to a legend icon. An
  icon that matches nothing is "unknown" - never guessed from size or colour.
* The identity block (name, RUID) is skipped and never returned. Header lines
  are read only for the fields named here.

Every inconsistency becomes a problem code on the row it affects, which the
importer turns into NEEDS REVIEW:

    ICON_MISSING / ICON_UNKNOWN / ICONS_MULTIPLE   the heading's status icon
    STATUS_TEXT_MISMATCH       icon disagrees with the "Needs:" line
    ALLOCATION_COUNT_MISMATCH  "Completed: n" differs from the listed courses
    COMPLETED_COUNT_MISMATCH   "Completed Requirements: k of n" differs
    PAGES_MISSING              the "i / N" page footer has gaps
    COURSE_LINE_UNREADABLE     a course code whose details could not be read
    READING_ORDER_UNRELIABLE   course details on a line without a course code
    UNCLASSIFIED_COURSE_ROW    a row of a shape CoursePilot does not know
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.transcript import term_code_for

PARSER_VERSION = "dn-audit-1"

#: Fragments within this many points vertically share a baseline.
BASELINE_TOLERANCE = 1.5
#: Chrome's header/footer band (points from the page edge).
MARGIN_BAND = 30.0

COURSE_CODE = re.compile(r"^\d{2}:\d{3}:\d{3}$")
PLACEMENT_CODE = re.compile(r"^[A-Z]{2}:\d{3}:[A-Z0-9]{3}$")
DETAIL = re.compile(
    r"^\(\s*(?P<credits>\d+(?:\.\d+)?)\s*,\s*(?P<term>(?:Fall|Spring|Summer|Winter)\s+\d{4}"
    r"|Placement)\s*,\s*(?P<grade>[^()]*?)\s*\)$")
LOOKS_LIKE_DETAIL = re.compile(r"^\(\s*[\d.]+\s*,")
NUMBER = re.compile(r"^\d{1,3}\.$")
HEADING = re.compile(r"^R(?P<number>\d+)\s*:\s*(?P<title>.+)$")
CODES = re.compile(r"\[([A-Za-z]{1,8})\]")
PRINTED_AT = re.compile(r"^(?P<date>\d{1,2}/\d{1,2}/\d{2}),\s*(?P<time>\d{1,2}:\d{2}\s*[AP]M)$")
DEGREE_ID = re.compile(r"[?&]degreeID=(\d+)")
SUBJECT_VERSION = re.compile(r"^(?P<subject>.+?)\s*\((?P<version>(?:Fall|Spring|Summer|Winter)"
                             r"\s+\d{4})\)$")
COUNT_OF = re.compile(r"^(\d+)\s+of\s+(\d+)$")
LETTER_LIKE = re.compile(r"^(?:[A-D][+]?|F|P|NC|W|NG|H|S|U|XF|TZ|PA|T[A-F][+]?)$")

COURSE_LEGEND = {
    "Indicates course has been successfully completed": "completed",
    "Indicates course is currently in progress": "current",
    "Indicates course is in the course plan": "planned",
}
REQUIREMENT_LEGEND = {
    "Indicates requirement has been fulfilled": "fulfilled",
    "Indicates requirement has not been fulfilled": "not_fulfilled",
    "Indicates requirement will be fulfilled with successful": "in_progress_or_planned",
}
COURSE_LISTS = {
    "Courses Used for this Program": "used",
    "Unused Courses for this Program": "unused",
    "Unused Courses for this Program - Courses Do Not Meet Minimum Grade Requirement(s)":
        "below_minimum_grade",
}


@dataclass
class Line:
    page: int
    number: int                       # 1-based line on its page, top to bottom
    y: float
    fragments: list[tuple[float, str]]

    @property
    def text(self) -> str:
        return " ".join(t for _, t in self.fragments)

    @property
    def first(self) -> str:
        return self.fragments[0][1] if self.fragments else ""


@dataclass
class Icon:
    page: int
    x: float
    y: float
    digest: str | None


@dataclass
class ParsedCourse:
    list_kind: str                    # requirement | used | unused | below_minimum_grade
    raw_text: str
    page: int
    line: int
    requirement_number: int | None = None
    course_code: str | None = None
    credits: str | None = None
    term_label: str | None = None
    term_code: str | None = None
    grade: str | None = None
    #: course (graded) | current | planned | placement | unclassified
    kind: str = "unclassified"
    problems: list[str] = field(default_factory=list)


@dataclass
class ParsedGoal:
    code: str
    title: str
    #: (course code, icon kind) - icon kind from the course legend, or unknown/missing
    courses: list[tuple[str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass
class ParsedRequirement:
    number: int
    title: str
    heading_text: str
    codes: list[str]
    page: int
    line: int
    #: fulfilled | not_fulfilled | in_progress_or_planned | unknown | missing
    icon: str = "missing"
    total: int | None = None
    completed: int | None = None
    needs: str | None = None
    goals: list[ParsedGoal] = field(default_factory=list)
    courses: list[ParsedCourse] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass
class ParsedBlock:
    index: int
    subject: str | None = None
    version_label: str | None = None
    report_date: str | None = None
    printed_at: str | None = None
    degree_id: str | None = None
    completed_requirements: str | None = None
    completed_credits: str | None = None
    requirements: list[ParsedRequirement] = field(default_factory=list)
    courses: list[ParsedCourse] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    legend: dict = field(default_factory=dict)
    pages: list[int] = field(default_factory=list)


@dataclass
class ParsedAudit:
    blocks: list[ParsedBlock]
    warnings: list[str]
    parser_version: str = PARSER_VERSION


# --------------------------------------------------------------------------
# lines and icons
# --------------------------------------------------------------------------


def _lines(page_no: int, layout: dict) -> tuple[list[Line], list[Line], list[Icon]]:
    """(body lines, margin lines, icons) of one page, body top to bottom."""
    height = float(layout.get("height") or 792)
    frags = sorted(((float(f["x"]), float(f["y"]), str(f["text"]).strip())
                    for f in layout.get("fragments", []) if str(f.get("text", "")).strip()),
                   key=lambda f: (-f[1], f[0]))
    rows: list[list[tuple[float, float, str]]] = []
    for frag in frags:
        if rows and abs(rows[-1][0][1] - frag[1]) <= BASELINE_TOLERANCE:
            rows[-1].append(frag)
        else:
            rows.append([frag])
    body, margin = [], []
    for row in rows:
        row.sort(key=lambda f: f[0])
        y = row[0][1]
        line = Line(page_no, 0, y, [(x, t) for x, _, t in row])
        (margin if y >= height - MARGIN_BAND or y <= MARGIN_BAND else body).append(line)
    for n, line in enumerate(body, 1):
        line.number = n
    icons = [Icon(page_no, float(i["x"]), float(i["y"]), i.get("digest"))
             for i in layout.get("images", [])]
    return body, margin, icons


def _icons_on(icons: list[Icon], line: Line) -> list[Icon]:
    return [i for i in icons if i.page == line.page
            and abs(i.y - line.y) <= BASELINE_TOLERANCE]


# --------------------------------------------------------------------------
# course rows
# --------------------------------------------------------------------------


def _course(list_kind: str, line: Line, code: str | None, detail: str | None,
            requirement: int | None, raw: str) -> ParsedCourse:
    row = ParsedCourse(list_kind=list_kind, raw_text=raw, page=line.page, line=line.number,
                       requirement_number=requirement, course_code=code)
    if code is None:
        row.problems.append("READING_ORDER_UNRELIABLE")
        return row
    m = DETAIL.match(detail or "")
    if m is None:
        row.problems.append("COURSE_LINE_UNREADABLE")
        return row
    row.credits = m.group("credits")
    term = " ".join(m.group("term").split())
    row.term_label = term
    grade = " ".join(m.group("grade").split())
    row.grade = grade or None
    if term == "Placement" or PLACEMENT_CODE.match(code):
        row.kind = "placement"
        return row
    season, year = term.split()
    row.term_code = term_code_for(season, year)
    if grade.lower() == "current":
        row.kind, row.grade = "current", None
    elif grade.lower() == "planned":
        row.kind, row.grade = "planned", None
    elif LETTER_LIKE.match(grade):
        row.kind = "course"
    else:
        row.kind = "unclassified"
        row.problems.append("UNCLASSIFIED_COURSE_ROW")
    return row


def _course_items(line: Line) -> list[tuple[str | None, str | None, str]]:
    """(code, detail, raw) per course record on a line. Numbered lines are split
    on their "n." items; an unnumbered line is one record."""
    frags = [t for _, t in line.fragments]
    if frags and NUMBER.match(frags[0]):
        items, current = [], []
        for t in frags:
            if NUMBER.match(t) and current:
                items.append(current)
                current = []
            current.append(t)
        items.append(current)
        groups = [g[1:] for g in items]
    else:
        groups = [frags]
    out = []
    for group in groups:
        code = next((t for t in group if COURSE_CODE.match(t) or PLACEMENT_CODE.match(t)), None)
        detail = next((t for t in group if LOOKS_LIKE_DETAIL.match(t)), None)
        if code is not None and detail is None:
            # The details may share the code's fragment ("01:999:101 ( 3, ...)").
            joined = " ".join(group)
            m = re.search(r"\(.*\)", joined)
            detail = m.group(0) if m else None
        out.append((code, detail, " ".join(group)))
    return out


def _is_course_line(line: Line) -> bool:
    return any(COURSE_CODE.match(t) or PLACEMENT_CODE.match(t) or LOOKS_LIKE_DETAIL.match(t)
               for _, t in line.fragments)


# --------------------------------------------------------------------------
# rule text: Core goal sets
# --------------------------------------------------------------------------


def _goal_sets(lines: list[Line], icons: list[Icon], course_legend: dict) -> list[ParsedGoal]:
    goals: list[ParsedGoal] = []
    label: list[str] = []
    in_set = in_courses = False
    courses: list[tuple[str, str]] = []
    for line in lines:
        line_icons = _icons_on(icons, line)
        for x, text in line.fragments:
            for token in re.findall(r"[{}()]|[^{}()]+", text):
                token = token.strip()
                if not token:
                    continue
                if token == "{":
                    in_set, in_courses, label, courses = True, False, [], []
                elif token == "}" and in_set:
                    title = " ".join(label)
                    codes = CODES.findall(title)
                    code = codes[-1] if codes else ""
                    name = CODES.sub("", title).strip()
                    goal = ParsedGoal(code=code, title=name, courses=courses)
                    if len(codes) > 1:
                        goal.problems.append("GOAL_CODES_AMBIGUOUS")
                    goals.append(goal)
                    in_set = in_courses = False
                elif token == "(" and in_set:
                    in_courses = True
                elif token == ")" and in_set:
                    in_courses = False
                elif in_set and in_courses:
                    for code in token.split():
                        if COURSE_CODE.match(code):
                            courses.append((code, _course_icon(x, line_icons, course_legend)))
                elif in_set:
                    label.append(token)
    return goals


def _course_icon(x: float, line_icons: list[Icon], legend: dict) -> str:
    """The icon right after a course code on its baseline (within ~70 pt)."""
    after = sorted((i for i in line_icons if x < i.x <= x + 75), key=lambda i: i.x)
    if not after:
        return "missing"
    return legend.get(after[0].digest, "unknown")


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------


#: A legend icon sits just left of its phrase; a tall icon (14x11, "will be
#: fulfilled") is centred on a two-line phrase, so up to an icon height below.
LEGEND_MAX_GAP = 20.0
LEGEND_MAX_DY = 8.0


def _legend(lines: list[Line], icons: list[Icon]) -> tuple[dict, dict]:
    course, requirement = {}, {}
    for line in lines:
        for x, text in line.fragments:
            for table, out in ((COURSE_LEGEND, course), (REQUIREMENT_LEGEND, requirement)):
                kind = table.get(text)
                if kind is None:
                    continue
                near = [i for i in icons if i.page == line.page and i.x < x
                        and x - i.x <= LEGEND_MAX_GAP and abs(i.y - line.y) <= LEGEND_MAX_DY]
                if near:
                    icon = min(near, key=lambda i: abs(i.y - line.y))
                    if icon.digest:
                        out[icon.digest] = kind
    return course, requirement


def _value(line: Line, label: str) -> str | None:
    """The value after a label: the following fragments, or the rest of the
    label's own fragment ("Date:  October 30 2026" is one fragment)."""
    if line.first == label:
        return " ".join(t for _, t in line.fragments[1:]).strip() or None
    if line.first.startswith(label + " "):
        rest = line.first[len(label):].strip()
        return " ".join([rest, *(t for _, t in line.fragments[1:])]).strip() or None
    return None


def _int(text: str | None) -> int | None:
    return int(text) if text and text.strip().isdigit() else None


def _check_requirement(req: ParsedRequirement) -> None:
    needs = (req.needs or "").strip()
    expects = ("not_fulfilled" if re.match(r"^\d+\s+more\b", needs)
               else "in_progress_or_planned" if needs.startswith("In Progress:")
               else "fulfilled" if needs.startswith("This requirement is") else None)
    if req.icon in ("fulfilled", "not_fulfilled", "in_progress_or_planned") and expects \
            and expects != req.icon:
        req.problems.append("STATUS_TEXT_MISMATCH")
    if req.completed is not None and req.completed != len(req.courses):
        req.problems.append("ALLOCATION_COUNT_MISMATCH")
    if any(c.problems for c in req.courses):
        req.problems.append("COURSE_ROWS_NEED_REVIEW")


def parse_audit(layouts: list[dict]) -> ParsedAudit:
    """Parse every degree block of a DN audit print."""
    body: list[Line] = []
    margins: dict[int, list[Line]] = {}
    icons: list[Icon] = []
    for page_no, layout in enumerate(layouts, 1):
        b, m, i = _lines(page_no, layout)
        body += b
        margins[page_no] = m
        icons += i

    starts = [k for k, line in enumerate(body) if line.text == "Degree Audit"]
    warnings: list[str] = []
    if not starts:
        return ParsedAudit(blocks=[], warnings=["NO_DEGREE_AUDIT_HEADING"])
    blocks = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(body)
        blocks.append(_parse_block(n, body[start:end], margins, icons))
    return ParsedAudit(blocks=blocks, warnings=warnings)


def _parse_block(index: int, lines: list[Line], margins: dict, icons: list[Icon]) -> ParsedBlock:
    block = ParsedBlock(index=index)
    block.pages = sorted({line.page for line in lines})
    for page in block.pages:
        for line in margins.get(page, []):
            m = PRINTED_AT.match(line.first)
            if m and block.printed_at is None:
                block.printed_at = f"{m.group('date')}, {m.group('time')}"
            d = DEGREE_ID.search(line.text)
            if d and block.degree_id is None:
                block.degree_id = d.group(1)
    _check_page_numbers(block, margins)

    course_legend, requirement_legend = _legend(lines, icons)
    block.legend = {"course": sorted(set(course_legend.values())),
                    "requirement": sorted(set(requirement_legend.values()))}
    if len(requirement_legend) < 3 or len(course_legend) < 3:
        block.warnings.append("LEGEND_INCOMPLETE")

    section = "header"
    req: ParsedRequirement | None = None
    req_part = "fields"
    rule_lines: list[Line] = []
    list_kind: str | None = None

    def close_requirement() -> None:
        nonlocal req, rule_lines
        if req is not None:
            req.goals = _goal_sets(rule_lines, icons, course_legend)
            _check_requirement(req)
            block.requirements.append(req)
        req, rule_lines = None, []

    for line in lines:
        text, first = line.text, line.first
        if section == "header":
            # Only named fields are read; the identity lines are skipped and
            # never stored or returned.
            if (v := _value(line, "Date:")) is not None:
                block.report_date = v
            elif (v := _value(line, "Report for Subject:")) is not None:
                m = SUBJECT_VERSION.match(v)
                block.subject, block.version_label = (
                    (m.group("subject"), m.group("version")) if m else (v, None))
            elif (v := _value(line, "Completed Credits:")) is not None:
                block.completed_credits = v
            elif (v := _value(line, "Completed Requirements:")) is not None:
                block.completed_requirements = v
            elif text == "Requirements":
                section = "requirements"
            continue
        if text in ("Conditions", "Reuse"):
            close_requirement()
            section = "skip"
            continue
        if text == "Courses":
            close_requirement()
            section = "courses"
            continue
        if text.startswith("Powered by"):
            close_requirement()
            section = "done"
            continue
        if section == "requirements":
            heading = HEADING.match(" ".join(t for _, t in line.fragments[1:])) \
                if first == "Requirement" else None
            if heading:
                close_requirement()
                title = heading.group("title").strip()
                req = ParsedRequirement(number=int(heading.group("number")), title=title,
                                        heading_text=f"Requirement {text.split(' ', 1)[1]}",
                                        codes=CODES.findall(title), page=line.page,
                                        line=line.number)
                found = [i for i in _icons_on(icons, line) if i.x < line.fragments[0][0]]
                if not found:
                    req.icon = "missing"
                    req.problems.append("ICON_MISSING")
                elif len(found) > 1:
                    req.icon = "unknown"
                    req.problems.append("ICONS_MULTIPLE")
                else:
                    req.icon = requirement_legend.get(found[0].digest, "unknown")
                    if req.icon == "unknown":
                        req.problems.append("ICON_UNKNOWN")
                req_part = "fields"
                continue
            if req is None:
                continue
            if (v := _value(line, "Total Courses:")) is not None:
                req.total = _int(v)
            elif (v := _value(line, "Completed:")) is not None:
                req.completed = _int(v)
            elif (v := _value(line, "Needs:")) is not None:
                req.needs = v
                req_part = "rule"
            elif first == "Notes:":
                req_part = "notes"
            elif re.match(r"^[A-Z][A-Za-z ]* Courses:$", text):
                req_part = "courses"
            elif req_part == "rule":
                rule_lines.append(line)
            elif req_part == "courses" and _is_course_line(line):
                for code, detail, raw in _course_items(line):
                    req.courses.append(_course("requirement", line, code, detail,
                                               req.number, raw))
            continue
        if section == "courses":
            if text in COURSE_LISTS:
                list_kind = COURSE_LISTS[text]
                continue
            if list_kind and _is_course_line(line):
                for code, detail, raw in _course_items(line):
                    block.courses.append(_course(list_kind, line, code, detail, None, raw))
    close_requirement()
    _check_counts(block)
    return block


def _check_page_numbers(block: ParsedBlock, margins: dict) -> None:
    seen, totals = set(), set()
    for page in block.pages:
        tokens = [t for line in margins.get(page, []) for _, t in line.fragments]
        joined = " ".join(tokens)
        m = re.search(r"(\d+)\s*/\s*(\d+)\s*$", joined)
        if m:
            seen.add(int(m.group(1)))
            totals.add(int(m.group(2)))
    if len(totals) == 1:
        total = totals.pop()
        if seen != set(range(1, total + 1)):
            block.warnings.append("PAGES_MISSING")


def _check_counts(block: ParsedBlock) -> None:
    m = COUNT_OF.match(block.completed_requirements or "")
    if not m:
        return
    done, total = int(m.group(1)), int(m.group(2))
    read_done = sum(r.icon in ("fulfilled", "in_progress_or_planned")
                    for r in block.requirements)
    if total != len(block.requirements):
        block.warnings.append("REQUIREMENT_COUNT_MISMATCH")
    if done != read_done:
        for r in block.requirements:
            r.problems.append("COMPLETED_COUNT_MISMATCH")


__all__ = ["PARSER_VERSION", "ParsedAudit", "ParsedBlock", "ParsedCourse", "ParsedGoal",
           "ParsedRequirement", "parse_audit"]
