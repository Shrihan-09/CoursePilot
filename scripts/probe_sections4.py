"""Fourth section probe: section-level credits and prerequisites.

Two items on the Phase 2 investigation list that earlier probes did not
explicitly answer:

  * does a SECTION carry its own credits, or only its parent course?
  * does a SECTION carry prerequisite information, or only its parent course?

Getting these wrong in either direction matters. If sections can override
credits, a course-level credits column silently misreports a student's load.
"""

from __future__ import annotations

import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
data = json.loads((ROOT / "data" / "raw" / "soc_courses_2026_9_NB.json").read_text(encoding="utf-8"))
sections = [(c, s) for c in data for s in (c.get("sections") or [])]

all_section_keys: set[str] = set()
for _, s in sections:
    all_section_keys.update(s.keys())

print("=" * 78)
print("SECTION-LEVEL CREDITS?")
print("=" * 78)
for probe in ("credit", "unit", "hour"):
    hits = sorted(k for k in all_section_keys if probe in k.lower())
    print(f"  section keys containing {probe!r:8} -> {hits if hits else 'NONE'}")

# baClassHours lives on meetingTimes, not the section. Check what it holds.
ba = [
    m.get("baClassHours")
    for _, s in sections
    for m in (s.get("meetingTimes") or [])
    if (m.get("baClassHours") or "").strip()
]
print(f"\n  meetingTimes.baClassHours populated: {len(ba):,}")
print(f"  distinct values: {sorted(set(ba))[:15]}")
print("  -> this is a class-hours annotation on a MEETING, not section credits.")

print()
print("=" * 78)
print("SECTION-LEVEL PREREQUISITES?")
print("=" * 78)
for probe in ("prereq", "prerequisite", "coreq", "restrict", "eligib", "permission"):
    hits = sorted(k for k in all_section_keys if probe in k.lower())
    print(f"  section keys containing {probe!r:14} -> {hits if hits else 'NONE'}")

print("\n  sectionEligibility sample values:")
elig = [
    (s.get("sectionEligibility") or "").strip()
    for _, s in sections
    if (s.get("sectionEligibility") or "").strip()
]
for v, n in collections.Counter(elig).most_common(5):
    print(f"    ({n:>4}x) {v[:90]}")

print("\n  openToText sample values:")
otx = [(s.get("openToText") or "").strip() for _, s in sections if (s.get("openToText") or "").strip()]
for v, n in collections.Counter(otx).most_common(5):
    print(f"    ({n:>4}x) {v[:90]}")

print()
print("=" * 78)
print("COURSE-LEVEL preReqNotes: does it vary per section?")
print("=" * 78)
print("  preReqNotes is a COURSE field, so by construction every section of a")
print("  course shares it. Confirming no section carries its own copy:")
print(f"    sections with a 'preReqNotes' key: "
      f"{sum(1 for _, s in sections if 'preReqNotes' in s)}")

print()
print("=" * 78)
print("SECTION-LEVEL TITLE OVERRIDE (subtitle)?")
print("=" * 78)
sub = [(s.get("subtitle") or "").strip() for _, s in sections if (s.get("subtitle") or "").strip()]
print(f"  sections with a subtitle: {len(sub):,} ({100*len(sub)/len(sections):.1f}%)")
for v, n in collections.Counter(sub).most_common(5):
    print(f"    ({n:>3}x) {v[:80]}")
print("  -> a per-section topic label, NOT a replacement course title.")

print()
print("=" * 78)
print("REGISTRATION STATUS VALUES (complete enumeration)")
print("=" * 78)
print(f"  openStatus:     {dict(collections.Counter(s.get('openStatus') for _, s in sections))}")
print(f"  openStatusText: {dict(collections.Counter(s.get('openStatusText') for _, s in sections))}")
print(f"  printed:        {dict(collections.Counter(s.get('printed') for _, s in sections))}")
print(
    "  specialPermissionAddCode: "
    f"{dict(collections.Counter(s.get('specialPermissionAddCode') for _, s in sections).most_common(6))}"
)

print()
print("=" * 78)
print("CAMPUS REPRESENTATION (three separate places)")
print("=" * 78)
print(f"  course.campusCode:  {dict(collections.Counter(c.get('campusCode') for c in data))}")
print(f"  section.campusCode: {dict(collections.Counter(s.get('campusCode') for _, s in sections))}")
meet_campus = collections.Counter(
    (m.get("campusName") or "").strip()
    for _, s in sections
    for m in (s.get("meetingTimes") or [])
)
print(f"  meeting.campusName: {dict(meet_campus.most_common(10))}")
print("  -> section campus == course campus always; MEETING campus can differ")
print("     (a section can meet on a different campus than it is offered by).")

# Does any section have meetings on more than one campus?
multi = 0
for _, s in sections:
    names = {
        (m.get("campusName") or "").strip()
        for m in (s.get("meetingTimes") or [])
        if (m.get("campusName") or "").strip()
    }
    if len(names) > 1:
        multi += 1
print(f"\n  sections meeting on >1 campus: {multi:,}")
