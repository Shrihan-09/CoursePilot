"""Profile the Rutgers SOC section data (Phase 2, Steps 1-2).

Sections are nested inside each course object in the archived courses.json
payload, so no new fetch is required. Everything here is measured against the
real 21 MB archive.

Investigation tooling, not part of the pipeline.
"""

from __future__ import annotations

import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
data = json.loads((ROOT / "data" / "raw" / "soc_courses_2026_9_NB.json").read_text(encoding="utf-8"))

sections = [(c, s) for c in data for s in (c.get("sections") or [])]
print(f"courses:  {len(data):,}")
print(f"sections: {len(sections):,}\n")


def pct(n: int, total: int = len(sections)) -> str:
    return f"{n:>7,} / {total:,} ({100 * n / total:5.1f}%)"


# ---------------------------------------------------------------- identity
print("=" * 78)
print("IDENTITY")
print("=" * 78)
idx = collections.Counter(s["index"] for _, s in sections)
print(f"  distinct 'index' values      {len(idx):,}")
print(f"  duplicated 'index' values    {sum(1 for v in idx.values() if v > 1)}")

# Candidate composite keys.
for name, keyfn in [
    ("index", lambda c, s: (s.get("index"),)),
    ("course+number", lambda c, s: (c["courseString"], c.get("supplementCode"), s.get("number"))),
    (
        "course+campus+number",
        lambda c, s: (c["courseString"], c.get("supplementCode"), c.get("campusCode"), s.get("number")),
    ),
]:
    ctr = collections.Counter(keyfn(c, s) for c, s in sections)
    dupes = sum(1 for v in ctr.values() if v > 1)
    print(f"  {name:24} {'UNIQUE' if dupes == 0 else f'{dupes} collisions'}")

# Does `number` repeat within one course? (lecture 01 vs lab 01 etc.)
per_course = collections.Counter(
    (c["courseString"], c.get("supplementCode"), c.get("campusCode"), s.get("number"))
    for c, s in sections
)
worst = per_course.most_common(3)
print(f"  worst course+campus+number:  {worst}")

# ------------------------------------------------------------ field presence
print()
print("=" * 78)
print("FIELD PRESENCE")
print("=" * 78)
fields = collections.Counter()
for _, s in sections:
    for k, v in s.items():
        if v not in (None, "", [], {}):
            fields[k] += 1
for k in sorted(fields, key=lambda k: -fields[k]):
    print(f"  {k:34} {pct(fields[k])}")

missing = sorted(set().union(*(set(s.keys()) for _, s in sections)) - set(fields))
print(f"\n  always empty/absent: {missing}")

# ------------------------------------------------- ENROLLMENT (critical check)
print()
print("=" * 78)
print("ENROLLMENT FIELDS (does SOC provide capacity / enrolled / waitlist?)")
print("=" * 78)
all_keys = set().union(*(set(s.keys()) for _, s in sections))
for probe in ("capacity", "enroll", "seat", "wait", "avail", "max", "limit", "count"):
    hits = sorted(k for k in all_keys if probe in k.lower())
    print(f"  keys containing {probe!r:12} -> {hits if hits else 'NONE'}")

# ------------------------------------------------------------- open status
print()
print("=" * 78)
print("OPEN STATUS")
print("=" * 78)
print("  openStatus:", dict(collections.Counter(s.get("openStatus") for _, s in sections)))
print("  openStatusText:", dict(collections.Counter(s.get("openStatusText") for _, s in sections).most_common(6)))
print("  printed:", dict(collections.Counter(s.get("printed") for _, s in sections)))

# ------------------------------------------------------------ meeting times
print()
print("=" * 78)
print("MEETING TIMES (one-to-many?)")
print("=" * 78)
mt_counts = collections.Counter(len(s.get("meetingTimes") or []) for _, s in sections)
for n in sorted(mt_counts):
    print(f"  {n} meeting pattern(s): {mt_counts[n]:>6,} sections")

meetings = [m for _, s in sections for m in (s.get("meetingTimes") or [])]
print(f"\n  total meeting rows: {len(meetings):,}")

mfields = collections.Counter()
for m in meetings:
    for k, v in m.items():
        if v not in (None, "", [], {}):
            mfields[k] += 1
print("\n  meeting field presence:")
for k in sorted(mfields, key=lambda k: -mfields[k]):
    print(f"    {k:28} {mfields[k]:>7,} / {len(meetings):,} ({100*mfields[k]/len(meetings):5.1f}%)")

print("\n  meetingDay values:", dict(collections.Counter(m.get("meetingDay") for m in meetings).most_common(12)))
print("  meetingModeDesc:", dict(collections.Counter(m.get("meetingModeDesc") for m in meetings).most_common(12)))
print("  campusName:", dict(collections.Counter(m.get("campusName") for m in meetings).most_common(10)))

# TBA detection
no_day = sum(1 for m in meetings if not (m.get("meetingDay") or "").strip())
no_start = sum(1 for m in meetings if not (m.get("startTimeMilitary") or "").strip())
no_bldg = sum(1 for m in meetings if not (m.get("buildingCode") or "").strip())
print(f"\n  meetings with NO meetingDay:        {no_day:,} ({100*no_day/len(meetings):.1f}%)")
print(f"  meetings with NO startTimeMilitary: {no_start:,} ({100*no_start/len(meetings):.1f}%)")
print(f"  meetings with NO buildingCode:      {no_bldg:,} ({100*no_bldg/len(meetings):.1f}%)")

# time format
times = {m.get("startTimeMilitary") for m in meetings if (m.get("startTimeMilitary") or "").strip()}
bad = [t for t in times if not (len(t) == 4 and t.isdigit())]
print(f"  distinct startTimeMilitary values:  {len(times):,}; malformed: {bad[:8]}")
mins = sorted(times)[:3]
maxs = sorted(times)[-3:]
print(f"  min/max startTimeMilitary: {mins} .. {maxs}")

# end < start?
weird = [
    (m.get("startTimeMilitary"), m.get("endTimeMilitary"))
    for m in meetings
    if (m.get("startTimeMilitary") or "").isdigit()
    and (m.get("endTimeMilitary") or "").isdigit()
    and m["endTimeMilitary"] <= m["startTimeMilitary"]
]
print(f"  meetings where end <= start: {len(weird)}  e.g. {weird[:5]}")

# ------------------------------------------------------------- instructors
print()
print("=" * 78)
print("INSTRUCTORS")
print("=" * 78)
inst_counts = collections.Counter(len(s.get("instructors") or []) for _, s in sections)
for n in sorted(inst_counts):
    print(f"  {n} instructor(s): {inst_counts[n]:>6,} sections")
sample_inst = next(s["instructors"] for _, s in sections if s.get("instructors"))
print(f"  instructor object keys: {sorted(sample_inst[0].keys())}")
print(f"  sample: {sample_inst[0]}")

# ------------------------------------------------------------ cross-listing
print()
print("=" * 78)
print("CROSS-LISTING / RESTRICTIONS / SESSION DATES")
print("=" * 78)
xl = sum(1 for _, s in sections if s.get("crossListedSections"))
print(f"  sections with crossListedSections:  {pct(xl)}")
xs = next((s for _, s in sections if s.get("crossListedSections")), None)
if xs:
    print(f"  crossListed keys: {sorted(xs['crossListedSections'][0].keys())}")
    print(f"  sample: {xs['crossListedSections'][0]}")
    print(f"  crossListedSectionsText: {xs.get('crossListedSectionsText')!r}")
print(f"  crossListedSectionType values: {dict(collections.Counter(s.get('crossListedSectionType') for _, s in sections))}")

for f in ("majors", "minors", "unitMajors", "honorPrograms", "comments"):
    n = sum(1 for _, s in sections if s.get(f))
    print(f"  sections with {f:16} {pct(n)}")

sd = sum(1 for _, s in sections if s.get("sessionDates"))
print(f"  sections with sessionDates:         {pct(sd)}")
sds = next((s.get("sessionDates") for _, s in sections if s.get("sessionDates")), None)
print(f"  sessionDates sample: {json.dumps(sds)[:200] if sds else 'NONE'}")

print(f"  sectionCourseType values: {dict(collections.Counter(s.get('sectionCourseType') for _, s in sections))}")
print(f"  examCode values: {dict(collections.Counter(s.get('examCode') for _, s in sections).most_common(8))}")

# ------------------------------------------ sections vs parent course linkage
print()
print("=" * 78)
print("SECTION -> PARENT COURSE LINKAGE")
print("=" * 78)
sample_keys = sorted(sections[0][1].keys())
course_link = [k for k in sample_keys if "course" in k.lower() or "subject" in k.lower() or "unit" in k.lower()]
print(f"  section fields referencing the course: {course_link}")
print("  -> sections are NESTED inside their course object; the parent is")
print("     structural, not a field on the section itself.")
