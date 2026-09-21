"""Build a Winter 2027 fixture that collides with the Fall 2026 fixture.

Phase 2.75 verified by hand that Fall 2026 and Winter 2027 coexist in
PostgreSQL with 21 shared registration indexes and zero collisions. This
fixture turns that one-off verification into an automated regression guard.

Selection rule: prefer Winter courses whose sections reuse an index that also
appears in the Fall 2026 fixture - those are the records that would break if
the section key ever lost `term_code`. Real records only; nothing invented.
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FALL_RAW = ROOT / "data" / "raw" / "soc_courses_2026_9_NB.json"
WINTER_RAW = ROOT / "data" / "raw" / "soc_courses_2027_0_NB.json"
FALL_FIXTURE = ROOT / "ingestion" / "tests" / "fixtures" / "soc_sections_sample.json"
OUT = ROOT / "ingestion" / "tests" / "fixtures" / "soc_winter_sections_sample.json"

winter = json.loads(WINTER_RAW.read_text(encoding="utf-8"))
fall_full = json.loads(FALL_RAW.read_text(encoding="utf-8"))
fall_fixture = json.loads(FALL_FIXTURE.read_text(encoding="utf-8"))

fall_fixture_idx = {s["index"] for c in fall_fixture for s in (c.get("sections") or [])}
fall_all_idx = {s["index"] for c in fall_full for s in (c.get("sections") or [])}

picked: list[dict] = []
seen: set[int] = set()


def take(course: dict, why: str) -> None:
    if id(course) in seen:
        return
    seen.add(id(course))
    picked.append(course)
    idxs = [s["index"] for s in (course.get("sections") or [])]
    print(f"  {course['courseString']:14} sections={idxs} -> {why}")


print("selecting Winter 2027 fixture records:")

# 1. Highest value: a Winter course whose section index also appears in the
#    FALL FIXTURE. Both terms land in one test database, so this is a genuine
#    in-test collision.
for c in winter:
    if any(s["index"] in fall_fixture_idx for s in (c.get("sections") or [])):
        take(c, "index collides with the Fall 2026 FIXTURE")

# 2. Next best: collides with the full Fall 2026 term (proven real reuse).
for c in winter:
    if len(picked) >= 6:
        break
    if any(s["index"] in fall_all_idx for s in (c.get("sections") or [])):
        take(c, "index collides with the full Fall 2026 term")

# 3. A course that also exists in Fall 2026 -> exercises "one course, two
#    offerings across terms".
def ckey(c: dict) -> tuple:
    return (
        c["offeringUnitCode"],
        c["subject"],
        c["courseNumber"],
        (c.get("supplementCode") or "").strip(),
    )


fall_keys = {ckey(c) for c in fall_fixture}
for c in winter:
    if ckey(c) in fall_keys:
        take(c, "same course as the Fall fixture -> second offering")
        break

# 4. Winter-only course (new to the database).
for c in winter:
    if ckey(c) not in {ckey(x) for x in fall_full} and len(picked) < 9:
        take(c, "Winter-only course (new to the DB)")
        break

# 5. Null credits, if present - Winter had several.
for c in winter:
    if c.get("credits") is None and len(picked) < 10:
        take(c, "null credits")
        break

# Trim sections so the fixture stays small, but ALWAYS keep the colliding one.
for c in picked:
    secs = c.get("sections") or []
    if len(secs) > 2:
        colliding = [s for s in secs if s["index"] in fall_all_idx]
        rest = [s for s in secs if s["index"] not in fall_all_idx]
        c["sections"] = (colliding + rest)[:2]

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(picked, indent=2), encoding="utf-8")

n_sec = sum(len(c.get("sections") or []) for c in picked)
collide_fixture = [
    s["index"] for c in picked for s in (c.get("sections") or []) if s["index"] in fall_fixture_idx
]
print(f"\nwrote {len(picked)} courses / {n_sec} sections -> {OUT.name}")
print(f"indexes colliding with the Fall FIXTURE: {collide_fixture}")
print(f"size: {OUT.stat().st_size:,} bytes")
