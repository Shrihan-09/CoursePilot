"""Build the section test fixture from the real cached SOC payload.

Same principle as the course fixture: tests run against REAL Rutgers records.
Hand-invented section data would encode our assumptions about the source,
which is exactly what the tests exist to check.

Each selection targets an edge case the investigation actually found.
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "raw" / "soc_courses_2026_9_NB.json"
OUT = ROOT / "ingestion" / "tests" / "fixtures" / "soc_sections_sample.json"

data = json.loads(SRC.read_text(encoding="utf-8"))

picked: list[dict] = []
seen: set[str] = set()


def take_course(course: dict, why: str, keep_sections: list[dict] | None = None) -> None:
    key = f"{course['courseString']}|{course.get('supplementCode')}|{course.get('campusCode')}"
    if key in seen:
        return
    seen.add(key)
    c = dict(course)
    if keep_sections is not None:
        c["sections"] = keep_sections
    picked.append(c)
    n = len(c.get("sections") or [])
    print(f"  {course['courseString']:14} supp={course.get('supplementCode')!r:6} "
          f"campus={course.get('campusCode')!r:5} sections={n:<3} -> {why}")


def find_course(cs: str, campus: str | None = None, supplement: str | None = None) -> dict | None:
    for c in data:
        if c["courseString"] != cs:
            continue
        if campus is not None and c.get("campusCode") != campus:
            continue
        if supplement is not None and c.get("supplementCode") != supplement:
            continue
        return c
    return None


print("selecting section fixture records:")

# 1. Baseline: a normal course whose section has a cross-listing + 2 meetings.
c = find_course("01:013:120")
take_course(c, "baseline; section has crossListedSections + 2 meeting patterns")

# 2/3. supplementCode pair -> two DIFFERENT courses, sections must not merge.
lec = find_course("01:750:193", supplement="  ")
lab = find_course("01:750:193", supplement="LB")
take_course(lec, "lecture half of the supplement pair (LEC+RECIT meetings)", (lec["sections"] or [])[:3])
take_course(lab, "lab half of the supplement pair (LAB meetings)", (lab["sections"] or [])[:3])

# 4/5. campusCode pair -> ONE course, two offerings, distinct section indexes.
nb = find_course("16:400:513", campus="NB")
ob = find_course("16:400:513", campus="OB")
take_course(nb, "NB half of the campus pair")
take_course(ob, "OB half of the campus pair (distinct section index)")

# 6. A section with meetings where end <= start (real Rutgers data).
weird = None
for c in data:
    for s in c.get("sections") or []:
        for m in s.get("meetingTimes") or []:
            st, en = (m.get("startTimeMilitary") or ""), (m.get("endTimeMilitary") or "")
            if st.isdigit() and en.isdigit() and en <= st:
                weird = (c, s)
                break
        if weird:
            break
    if weird:
        break
if weird:
    take_course(weird[0], "meeting with end <= start (must WARN, not reject)", [weird[1]])

# 7. A section listing the same instructor name twice.
dupe_inst = None
for c in data:
    for s in c.get("sections") or []:
        names = [(i.get("name") or "").strip() for i in (s.get("instructors") or [])]
        if len(names) > 1 and len(set(names)) < len(names):
            dupe_inst = (c, s)
            break
    if dupe_inst:
        break
if dupe_inst:
    take_course(dupe_inst[0], "same instructor name listed twice (ordinal key required)", [dupe_inst[1]])

# 8. A fully TBA section: no day, no time, no building.
tba = None
for c in data:
    for s in c.get("sections") or []:
        ms = s.get("meetingTimes") or []
        if ms and all(not (m.get("meetingDay") or "").strip() for m in ms):
            tba = (c, s)
            break
    if tba:
        break
if tba:
    take_course(tba[0], "fully TBA meetings (no day/time/building)", [tba[1]])

# 9. An online section.
online = None
for c in data:
    for s in c.get("sections") or []:
        if any(m.get("meetingModeCode") == "90" for m in (s.get("meetingTimes") or [])):
            online = (c, s)
            break
    if online:
        break
if online:
    take_course(online[0], "ONLINE INSTRUCTION(INTERNET) meeting mode", [online[1]])

# 10. A section with 3+ meeting patterns.
many = None
for c in data:
    for s in c.get("sections") or []:
        if len(s.get("meetingTimes") or []) >= 3:
            many = (c, s)
            break
    if many:
        break
if many:
    take_course(many[0], f"{len(many[1]['meetingTimes'])} meeting patterns (one-to-many proof)", [many[1]])

# 11. A CLOSED section, and one with a non-numeric section number.
closed = next(((c, s) for c in data for s in (c.get("sections") or []) if s.get("openStatus") is False), None)
if closed:
    take_course(closed[0], "CLOSED section", [closed[1]])

alpha = next(
    ((c, s) for c in data for s in (c.get("sections") or []) if not (s.get("number") or "").isdigit()),
    None,
)
if alpha:
    take_course(alpha[0], f"non-numeric section number {alpha[1]['number']!r}", [alpha[1]])

# 12. A section with 0 instructors.
noinst = next(((c, s) for c in data for s in (c.get("sections") or []) if not s.get("instructors")), None)
if noinst:
    take_course(noinst[0], "0 instructors (instructor TBA)", [noinst[1]])

total_sections = sum(len(c.get("sections") or []) for c in picked)
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(picked, indent=2), encoding="utf-8")
print(f"\nwrote {len(picked)} courses / {total_sections} sections -> {OUT}")
print(f"   ({OUT.stat().st_size:,} bytes)")
