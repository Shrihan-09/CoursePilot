"""Build a Mathematics-major course fixture from the real SOC archives (Phase 6.0).

The multi-program tests need the courses the Mathematics (Option A) prose
names, plus enough 300+ mathematics courses to fill "eight 300- to 400-level
mathematics courses". Real records only, like make_cs_fixture.py -
hand-written courses would encode our assumptions instead of testing them.

Selection:
  * every course named in the Option A prose that SOC actually offers
  * 300-499 mathematics courses, so eight upper-level slots can be filled
  * 01:640:491, which the prose EXCLUDES - it must never count

Three archived terms are searched because several named courses (01:640:312,
412, 452, 492 and 14:332:252) are offered only in spring or only in an earlier
fall - they are absent from Fall 2026 alone, and absent from the development
database, where the loader reports them unresolved rather than creating them.
A course found in no archive is printed MISSING and left out: a fixture must
not contain a course Rutgers did not publish.
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARCHIVES = [
    ROOT / "data" / "raw" / "soc_courses_2026_9_NB.json",
    ROOT / "data" / "raw" / "soc_courses_2026_1_NB.json",
    ROOT / "data" / "raw" / "soc_courses_2025_9_NB.json",
]
OUT = ROOT / "ingestion" / "tests" / "fixtures" / "soc_math_courses_sample.json"

NAMED = [
    "01:640:151", "01:640:152", "01:640:251", "01:640:250",
    "01:640:244", "01:640:252",
    "01:198:107", "01:198:111", "14:332:252",
    "01:640:311", "01:640:312", "01:640:411", "01:640:412",
    "01:640:350", "01:640:351", "01:640:451", "01:640:452",
    "01:640:491", "01:640:492",
]

by_string: dict[str, dict] = {}
for archive in ARCHIVES:
    for c in json.loads(archive.read_text(encoding="utf-8")):
        if (c.get("supplementCode") or "").strip() == "":
            # First archive wins: the newest term is listed first.
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


print("selecting Mathematics fixture courses:")
for cs in NAMED:
    take(cs, "named in catalog prose")

upper = sorted(
    cs for cs in by_string
    if cs.startswith("01:640:")
    and cs.split(":")[2].isdigit()
    and 300 <= int(cs.split(":")[2]) <= 499
    and cs not in seen
)
for cs in upper[:10]:
    take(cs, "300-499 mathematics")

picked.sort(key=lambda c: c["courseString"])
OUT.write_text(json.dumps(picked, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"\n{len(picked)} real SOC course records -> {OUT.relative_to(ROOT)}")
