"""Build a CS-major course fixture from the real SOC archive.

The audit tests need the courses the curated CS requirement names, plus enough
300-level CS electives to exercise choose-N and its constraints. Real records
only - hand-written courses would encode our assumptions instead of testing
them.

Selection:
  * the 9 courses named in the catalog prose (6 CS core + 3 math)
  * 300+ CS electives (for CS_ELECTIVES eligibility)
  * one sub-300 CS course (must NOT satisfy the 300-level constraint)
  * one non-CS course (exercises max_outside_subject)
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "raw" / "soc_courses_2026_9_NB.json"
OUT = ROOT / "ingestion" / "tests" / "fixtures" / "soc_cs_courses_sample.json"

NAMED = [
    "01:198:111", "01:198:112", "01:198:205", "01:198:206", "01:198:211", "01:198:344",
    "01:640:151", "01:640:152", "01:640:250",
]

data = json.loads(SRC.read_text(encoding="utf-8"))
by_string: dict[str, dict] = {}
for c in data:
    if (c.get("supplementCode") or "").strip() == "":
        by_string.setdefault(c["courseString"], c)

picked: list[dict] = []
seen: set[str] = set()


def take(cs: str, why: str) -> bool:
    if cs in seen:
        return False
    course = by_string.get(cs)
    if course is None:
        print(f"  MISSING {cs} ({why})")
        return False
    seen.add(cs)
    picked.append(course)
    print(f"  {cs:14} cr={course.get('credits')!s:5} {why}")
    return True


print("selecting CS fixture courses:")
for cs in NAMED:
    take(cs, "named in catalog prose")

# 300+ CS electives, excluding 344 which is already a required course.
electives = sorted(
    cs for cs in by_string
    if cs.startswith("01:198:")
    and cs.split(":")[2].isdigit()
    and int(cs.split(":")[2]) >= 300
    and cs not in seen
)
for cs in electives[:8]:
    take(cs, "300+ CS elective")

# A sub-300 CS course: eligible for nothing here, and must not count toward
# the "at least two at the 300 level" constraint.
for cs in sorted(by_string):
    if cs.startswith("01:198:") and cs.split(":")[2].isdigit() and int(cs.split(":")[2]) < 300:
        if cs not in seen and take(cs, "sub-300 CS (must NOT meet 300-level rule)"):
            break

# A non-CS, non-math course for the max_outside_subject constraint.
for cs in sorted(by_string):
    unit, subject, _ = cs.split(":")
    if subject not in ("198", "640") and cs not in seen:
        if take(cs, "non-CS (exercises max_outside_subject)"):
            break

for c in picked:
    if c.get("sections"):
        c["sections"] = c["sections"][:1]

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(picked, indent=2), encoding="utf-8")
print(f"\nwrote {len(picked)} courses -> {OUT.name} ({OUT.stat().st_size:,} bytes)")
