"""Positioned WebReg print schedule parser; never OCR or inferred weekdays."""

from __future__ import annotations

import re

PARSER_VERSION = "webreg-layout-3"
MAX_ROWS = 100
COURSE = r"\d{2}:\d{3}:\d{3}"
# Grid cells print course:section, then the index after ONE separator. A real
# browser-saved WebReg print uses ":" (verified locally, Phase 7.2.2); "-" is the
# form the first fixtures assumed. Either way exactly five digits, nothing else.
GRID_ID = re.compile(rf"(?<!\d)({COURSE}):([A-Za-z0-9]{{1,8}})[-:](\d{{5}})(?!\d)")
ARRANGED_ID = re.compile(rf"\(({COURSE}):([A-Za-z0-9]{{1,8}})\)\s+(\d{{5}})(?!\d)")
FALLBACK_ID = re.compile(rf"\(({COURSE}):([A-Za-z0-9]{{1,8}})\)(?!\s*\d)")
TERM = re.compile(r"\b(Spring|Summer|Fall|Winter)\s+(\d{4})\b", re.I)
ARRANGED = re.compile(r"By Arrangement,?\s*Off-campus,?\s*or Other Courses", re.I)
DAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}


class ScheduleParseError(Exception):
    """Closed error codes, never document content."""


def _lines(fragments):
    groups = []
    for item in sorted(fragments, key=lambda f: (-f["y"], f["x"])):
        for group in groups:
            if abs(group[0]["y"] - item["y"]) <= 2:
                group.append(item)
                break
        else:
            groups.append([item])
    return [
        " ".join(f["text"].strip() for f in sorted(group, key=lambda f: f["x"])) for group in groups
    ]


def parse_schedule(pages: list[str], layout: list[dict] | None = None) -> list[dict]:
    if not layout or len(layout) != len(pages):
        raise ScheduleParseError("UNSUPPORTED_LAYOUT")
    text = "\n".join(pages)
    if not re.search(r"\bWebReg\b", text, re.I) or not re.search(
        r"View\s*/\s*Print\s+Schedule", text, re.I
    ):
        raise ScheduleParseError("UNSUPPORTED_LAYOUT")
    terms = {(season.lower(), year) for season, year in TERM.findall(text)}
    if len(terms) != 1:
        raise ScheduleParseError("SCHEDULE_TERM_AMBIGUOUS")
    season, year = next(iter(terms))
    term = year + {"winter": "0", "spring": "1", "summer": "7", "fall": "9"}[season]
    rows, seen = [], {}
    for page in layout:
        fragments = page.get("fragments", [])
        if not fragments or page.get("width", 0) <= 0 or page.get("height", 0) <= 0:
            raise ScheduleParseError("UNSUPPORTED_LAYOUT")
        if any(
            not isinstance(f.get("text"), str)
            or not isinstance(f.get("x"), (int, float))
            or not isinstance(f.get("y"), (int, float))
            for f in fragments
        ):
            raise ScheduleParseError("UNSUPPORTED_LAYOUT")
        day_headers = [f for f in fragments if f["text"].strip().lower() in DAYS]
        arrangement = [f for f in fragments if ARRANGED.search(f["text"])]
        arranged_y = max((f["y"] for f in arrangement), default=-1)
        if not day_headers and not arrangement:
            raise ScheduleParseError("UNSUPPORTED_LAYOUT")
        columns = sorted(day_headers, key=lambda f: f["x"])
        if columns and (
            len({f["text"].strip().lower() for f in columns}) != len(columns)
            or max(f["y"] for f in columns) - min(f["y"] for f in columns) > 5
        ):
            raise ScheduleParseError("UNSUPPORTED_LAYOUT")
        boundaries = [(columns[i]["x"] + columns[i + 1]["x"]) / 2 for i in range(len(columns) - 1)]
        regions = {}
        identity_objects = [f for f in fragments if re.search(COURSE, f["text"])]
        for fragment in fragments:
            if arranged_y >= 0 and fragment["y"] < arranged_y - 2:
                region = "arranged"
            elif columns and arranged_y < fragment["y"] < min(f["y"] for f in columns) - 2:
                region = sum(fragment["x"] >= x for x in boundaries)
            else:
                continue
            regions.setdefault(region, []).append(fragment)
        recognized = 0
        for region, objects in regions.items():
            for line in _lines(objects):
                if not re.search(COURSE, line):
                    continue
                matches = list((ARRANGED_ID if region == "arranged" else GRID_ID).finditer(line))
                fallback = (
                    list(FALLBACK_ID.finditer(line)) if region == "arranged" and not matches else []
                )
                if len(matches) + len(fallback) != 1:
                    raise ScheduleParseError("UNSUPPORTED_LAYOUT")
                match = (matches or fallback)[0]
                if re.search(COURSE, line[: match.start()] + line[match.end() :]):
                    raise ScheduleParseError("UNSUPPORTED_LAYOUT")
                course, section = match.group(1), match.group(2).upper()
                index = match.group(3) if matches else None
                key = (course, section)
                if key in seen:
                    if seen[key] != index:
                        raise ScheduleParseError("SCHEDULE_IDENTIFIERS_CONFLICT")
                else:
                    seen[key] = index
                    rows.append(
                        {
                            "term_code": term,
                            "course_string": course,
                            "section_number": section,
                            "index_number": index,
                        }
                    )
                recognized += 1
                if len(rows) > MAX_ROWS:
                    raise ScheduleParseError("TOO_MANY_SCHEDULE_ROWS")
        if len(identity_objects) > recognized:
            raise ScheduleParseError("UNSUPPORTED_LAYOUT")
    if not rows:
        raise ScheduleParseError("UNSUPPORTED_LAYOUT")
    return rows
