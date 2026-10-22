"""Structural check of a SOC term payload against a baseline term (Phase 7.3; Phase 2.5 method).

Run BEFORE a newly published term is ingested: an undocumented API can change
shape silently, and a shape change must stop the load rather than be
"handled". Compares, against the baseline payload:

* the key sets of course, section and meeting objects (new / missing fields);
* meetingDay vocabulary (values outside M T W H F S U and blank);
* meetings whose end is not after their start;
* restriction lists (majors / unitMajors / minors / sectionEligibility) shape;
* coreCodes entry shape.

Returns findings; `blocking` ones stop ingestion. Counts and field names only.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

DAYS = {"M", "T", "W", "H", "F", "S", "U", ""}
RESTRICTION_LISTS = ("majors", "unitMajors", "minors")


@dataclass
class StructureReport:
    courses: int = 0
    sections: int = 0
    meetings: int = 0
    new_fields: dict = field(default_factory=dict)        # level -> sorted names
    missing_fields: dict = field(default_factory=dict)    # level -> sorted names
    unknown_days: dict = field(default_factory=dict)      # value -> count
    end_not_after_start: int = 0
    restriction_shapes: dict = field(default_factory=dict)
    core_code_shapes: dict = field(default_factory=dict)
    blocking: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocking


def _keys(objs):
    keys = set()
    for o in objs:
        keys |= set(o)
    return keys


def _minutes(t):
    if not t or len(t) != 4 or not t.isdigit():
        return None
    return int(t[:2]) * 60 + int(t[2:])


def _parts(courses):
    sections = [s for c in courses for s in c.get("sections") or []]
    meetings = [m for s in sections for m in s.get("meetingTimes") or []]
    return sections, meetings


def check(payload: list, baseline: list) -> StructureReport:
    r = StructureReport()
    sections, meetings = _parts(payload)
    b_sections, b_meetings = _parts(baseline)
    r.courses, r.sections, r.meetings = len(payload), len(sections), len(meetings)
    for level, now, base in (("course", payload, baseline), ("section", sections, b_sections),
                             ("meeting", meetings, b_meetings)):
        new, gone = sorted(_keys(now) - _keys(base)), sorted(_keys(base) - _keys(now))
        if new:
            r.new_fields[level] = new
        if gone:
            r.missing_fields[level] = gone
            r.blocking.append(f"missing_{level}_fields")
    days = collections.Counter(m.get("meetingDay") or "" for m in meetings)
    r.unknown_days = {d: n for d, n in days.items() if d not in DAYS}
    if r.unknown_days:
        r.blocking.append("unknown_meeting_day")
    for m in meetings:
        start = _minutes(m.get("startTimeMilitary"))
        end = _minutes(m.get("endTimeMilitary"))
        if start is not None and end is not None and end <= start:
            r.end_not_after_start += 1
    for name in RESTRICTION_LISTS:
        shapes = collections.Counter(
            ",".join(sorted(e)) if isinstance(e, dict) else type(e).__name__
            for s in sections for e in s.get(name) or [])
        r.restriction_shapes[name] = dict(shapes)
    elig = collections.Counter(type(s.get("sectionEligibility")).__name__ for s in sections)
    r.restriction_shapes["sectionEligibility"] = dict(elig)
    r.core_code_shapes = dict(collections.Counter(
        ",".join(sorted(e)) if isinstance(e, dict) else type(e).__name__
        for c in payload for e in c.get("coreCodes") or []))
    base_core = {",".join(sorted(e)) for c in baseline for e in c.get("coreCodes") or []
                 if isinstance(e, dict)}
    if set(r.core_code_shapes) - base_core - {"str"}:
        r.blocking.append("core_code_shape_changed")
    return r


__all__ = ["StructureReport", "check"]
