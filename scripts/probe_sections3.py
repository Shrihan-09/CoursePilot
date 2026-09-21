"""Third section probe: do meetings and instructors have natural keys?

Idempotency depends entirely on this. If child rows have no stable identity in
the source, the loader cannot upsert them and must use replace-by-parent.
"""

from __future__ import annotations

import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
data = json.loads((ROOT / "data" / "raw" / "soc_courses_2026_9_NB.json").read_text(encoding="utf-8"))
sections = [(c, s) for c in data for s in (c.get("sections") or [])]

print("=" * 78)
print("MEETING NATURAL KEY?")
print("=" * 78)

def mkey(m: dict) -> tuple:
    return (
        (m.get("meetingDay") or "").strip(),
        (m.get("startTimeMilitary") or "").strip(),
        (m.get("endTimeMilitary") or "").strip(),
        (m.get("meetingModeCode") or "").strip(),
        (m.get("buildingCode") or "").strip(),
        (m.get("roomNumber") or "").strip(),
        (m.get("campusLocation") or "").strip(),
    )

dupe_sections = 0
dupe_examples = []
total_dupe_rows = 0
for c, s in sections:
    ms = s.get("meetingTimes") or []
    ctr = collections.Counter(mkey(m) for m in ms)
    d = [k for k, v in ctr.items() if v > 1]
    if d:
        dupe_sections += 1
        total_dupe_rows += sum(v - 1 for v in ctr.values() if v > 1)
        if len(dupe_examples) < 5:
            dupe_examples.append((c["courseString"], s["index"], len(ms), d[0], ctr[d[0]]))

print(f"  sections with DUPLICATE identical meeting rows: {dupe_sections:,}")
print(f"  surplus duplicate meeting rows total:           {total_dupe_rows:,}")
for ex in dupe_examples:
    print(f"    {ex[0]} idx={ex[1]} meetings={ex[2]} key={ex[3]} x{ex[4]}")

print("\n  => if >0, (section, day, time, mode, room) is NOT a natural key.")

print()
print("=" * 78)
print("INSTRUCTOR NATURAL KEY?")
print("=" * 78)
dupe_inst = 0
inst_examples = []
for c, s in sections:
    names = [(i.get("name") or "").strip() for i in (s.get("instructors") or [])]
    ctr = collections.Counter(names)
    d = [k for k, v in ctr.items() if v > 1]
    if d:
        dupe_inst += 1
        if len(inst_examples) < 5:
            inst_examples.append((c["courseString"], s["index"], names))
print(f"  sections with the SAME instructor name listed twice: {dupe_inst:,}")
for ex in inst_examples:
    print(f"    {ex[0]} idx={ex[1]} {ex[2]}")

blank = sum(
    1 for _, s in sections for i in (s.get("instructors") or []) if not (i.get("name") or "").strip()
)
print(f"  instructor entries with blank name: {blank}")

names = collections.Counter(
    (i.get("name") or "").strip() for _, s in sections for i in (s.get("instructors") or [])
)
print(f"  distinct instructor names: {len(names):,}")
print(f"  most common: {names.most_common(5)}")

print()
print("=" * 78)
print("CROSS-LISTING NATURAL KEY?")
print("=" * 78)
dupe_xl = 0
for c, s in sections:
    keys = [
        (
            x.get("offeringUnitCode"),
            x.get("subjectCode"),
            x.get("courseNumber"),
            x.get("supplementCode"),
            x.get("sectionNumber"),
            x.get("registrationIndex"),
        )
        for x in (s.get("crossListedSections") or [])
    ]
    ctr = collections.Counter(keys)
    if any(v > 1 for v in ctr.values()):
        dupe_xl += 1
print(f"  sections with duplicate cross-listing rows: {dupe_xl}")

xl_blank_idx = sum(
    1
    for _, s in sections
    for x in (s.get("crossListedSections") or [])
    if not (x.get("registrationIndex") or "").strip()
)
print(f"  cross-listing rows with blank registrationIndex: {xl_blank_idx}")

print()
print("=" * 78)
print("DO ALL SECTION PARENT COURSES EXIST AS COURSE RECORDS?")
print("=" * 78)
# Sections are nested, so the parent always exists in the payload. The real
# question is whether the parent's natural key is well-formed.
bad_parent = [
    (c.get("courseString"), s["index"])
    for c, s in sections
    if not (c.get("offeringUnitCode") and c.get("subject") and c.get("courseNumber"))
]
print(f"  sections whose parent lacks a complete natural key: {len(bad_parent)}")

print()
print("=" * 78)
print("SUBTITLE / SECTION NOTES / COMMENTS shape")
print("=" * 78)
sub = next((s for _, s in sections if (s.get("subtitle") or "").strip()), None)
print(f"  subtitle sample: {sub.get('subtitle')!r}" if sub else "  none")
cm = next((s for _, s in sections if s.get("comments")), None)
if cm:
    print(f"  comments keys: {sorted(cm['comments'][0].keys())}")
    print(f"  comments sample: {json.dumps(cm['comments'][0])[:180]}")
    print(f"  commentsText: {cm.get('commentsText')!r}"[:200])
