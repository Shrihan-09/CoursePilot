"""Build the test fixture from the real cached SOC payload.

Tests run against REAL Rutgers records, not invented ones. Hand-written test
data encodes our assumptions about the source, which is exactly what the tests
are supposed to be checking. The selection below deliberately includes every
edge case the investigation turned up.
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "raw" / "soc_courses_2026_9_NB.json"
OUT = ROOT / "ingestion" / "tests" / "fixtures" / "soc_courses_sample.json"

data = json.loads(SRC.read_text(encoding="utf-8"))
by_cs: dict[str, list[dict]] = {}
for c in data:
    by_cs.setdefault(c["courseString"], []).append(c)

picked: list[dict] = []
seen: set[int] = set()


def take(course: dict, why: str) -> None:
    if id(course) in seen:
        return
    seen.add(id(course))
    picked.append(course)
    print(f"  {course['courseString']:14} supp={course.get('supplementCode')!r:6} "
          f"campus={course.get('campusCode')!r:6} credits={course.get('credits')!r:6} -> {why}")


print("selecting fixture records:")

# 1+2. supplementCode duplicate: lecture vs lab, same courseString.
for c in by_cs.get("01:750:193", []):
    take(c, "supplementCode duplicate (lecture/lab)")

# 3+4. campusCode duplicate: same course, two campuses.
for c in by_cs.get("16:400:513", []):
    take(c, "campusCode duplicate (NB/OB)")

# 5. null credits
take(next(c for c in data if c.get("credits") is None), "null credits")

# 6. fractional credits
take(next(c for c in data if isinstance(c.get("credits"), float)), "fractional credits")

# 7. preReqNotes containing HTML markup
take(
    next(c for c in data if "<em>" in (c.get("preReqNotes") or "")),
    "preReqNotes with embedded HTML",
)

# 8. expandedTitle differs from abbreviated title
take(
    next(
        c
        for c in data
        if (c.get("expandedTitle") or "").strip()
        and c["expandedTitle"].strip() != c["title"].strip()
        and c.get("credits") is not None
    ),
    "expandedTitle differs from title",
)

# 9. graduate level
take(next(c for c in data if c.get("level") == "G" and c.get("credits")), "graduate level")

# 10. plain undergraduate course, no surprises
take(
    next(
        c
        for c in data
        if c.get("level") == "U"
        and isinstance(c.get("credits"), int)
        and not (c.get("preReqNotes") or "").strip()
        and len(by_cs[c["courseString"]]) == 1
    ),
    "baseline undergraduate course",
)

# Trim sections to keep the fixture small; sections are not ingested yet.
for c in picked:
    if c.get("sections"):
        c["sections"] = c["sections"][:1]

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(picked, indent=2), encoding="utf-8")
print(f"\nwrote {len(picked)} records -> {OUT} ({OUT.stat().st_size:,} bytes)")
