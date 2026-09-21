"""Investigate why courseString is not unique in the SOC payload.

This determines the natural key for the course table. Getting it wrong means
either silently losing courses or creating duplicates on re-ingest.
"""

from __future__ import annotations

import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
data = json.loads((ROOT / "data" / "raw" / "soc_courses_2026_9_NB.json").read_text(encoding="utf-8"))

counts = collections.Counter(c["courseString"] for c in data)
dupes = [k for k, v in counts.items() if v > 1]
print(f"duplicated courseStrings: {len(dupes)}\n")

for cs in dupes:
    rows = [c for c in data if c["courseString"] == cs]
    print(f"=== {cs}  ({len(rows)} rows)")
    for r in rows:
        print(
            f"    supplement={r.get('supplementCode')!r:8} level={r.get('level')!r} "
            f"campus={r.get('campusCode')!r} credits={r.get('credits')!r} "
            f"sections={len(r.get('sections') or [])} title={r.get('title')!r:40}"
        )
    # Which fields actually differ between the duplicate rows?
    differing = set()
    keys = set().union(*(set(r.keys()) for r in rows))
    for k in keys:
        vals = {json.dumps(r.get(k), sort_keys=True, default=str) for r in rows}
        if len(vals) > 1:
            differing.add(k)
    print(f"    differing fields: {sorted(differing)}\n")

print("\n--- candidate composite key test ---")
for key_fields in (
    ("courseString",),
    ("courseString", "supplementCode"),
    ("courseString", "level"),
    ("courseString", "supplementCode", "level"),
    ("offeringUnitCode", "subject", "courseNumber", "supplementCode", "level"),
):
    c = collections.Counter(tuple(str(r.get(f)) for f in key_fields) for r in data)
    d = sum(1 for v in c.values() if v > 1)
    status = "UNIQUE" if d == 0 else f"{d} collisions"
    print(f"  {' + '.join(key_fields):62} {status}")

print("\n--- section index uniqueness (across all courses) ---")
idx = collections.Counter(s["index"] for c in data for s in (c.get("sections") or []))
print(f"  sections total: {sum(idx.values()):,} | distinct index: {len(idx):,}")
print(f"  duplicated index values: {sum(1 for v in idx.values() if v > 1)}")

print("\n--- title vs expandedTitle ---")
both = [c for c in data if (c.get("expandedTitle") or "").strip()]
diff = [c for c in both if c["expandedTitle"].strip() != c["title"].strip()]
print(f"  have expandedTitle: {len(both):,} | differs from title: {len(diff):,}")
for c in diff[:3]:
    print(f"    {c['courseString']}: title={c['title']!r}")
    print(f"    {'':13} expanded={c['expandedTitle']!r}")

print("\n--- coreCodes shape ---")
cc = next(c["coreCodes"] for c in data if c.get("coreCodes"))
print(json.dumps(cc[0], indent=2)[:600])
