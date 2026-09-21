"""Phase 2.5 Steps 5-6: compare two real Rutgers terms.

Spring 2027 is not published, so Spring 2026 stands in as the comparison term
- it is the adjacent real Spring, and the point here is to learn how the
schema behaves when a SECOND term arrives, not to ingest that term.

Pure measurement against archived payloads. Nothing is written to a database.
"""

from __future__ import annotations

import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

A_LABEL, A_FILE = "Fall 2026", "soc_courses_2026_9_NB.json"
B_LABEL, B_FILE = "Spring 2026", "soc_courses_2026_1_NB.json"

a = json.loads((RAW / A_FILE).read_text(encoding="utf-8"))
b = json.loads((RAW / B_FILE).read_text(encoding="utf-8"))


def course_key(c: dict) -> tuple[str, str, str, str]:
    """The Phase 1 natural key: campus deliberately excluded."""
    return (
        c["offeringUnitCode"],
        c["subject"],
        c["courseNumber"],
        (c.get("supplementCode") or "").strip(),
    )


def offering_key(c: dict, term: str) -> tuple:
    return (*course_key(c), term, c.get("campusCode"))


print("=" * 78)
print(f"COURSE IDENTITY:  {A_LABEL}  vs  {B_LABEL}")
print("=" * 78)

ka = collections.Counter(course_key(c) for c in a)
kb = collections.Counter(course_key(c) for c in b)

print(f"  {A_LABEL:12} records={len(a):>6,}  distinct natural keys={len(ka):>6,}")
print(f"  {B_LABEL:12} records={len(b):>6,}  distinct natural keys={len(kb):>6,}")
print(f"  {A_LABEL} within-term key collisions: {sum(1 for v in ka.values() if v > 1)}")
print(f"  {B_LABEL} within-term key collisions: {sum(1 for v in kb.values() if v > 1)}")

sa, sb = set(ka), set(kb)
print(f"\n  in BOTH terms:        {len(sa & sb):>6,}")
print(f"  only in {A_LABEL}:   {len(sa - sb):>6,}")
print(f"  only in {B_LABEL}: {len(sb - sa):>6,}")
print("  (a course absent from one term is simply not offered - not an error)")

# Do shared courses agree on their course-level attributes?
print("\n  ATTRIBUTE DRIFT among courses present in both terms:")
amap = {course_key(c): c for c in a}
bmap = {course_key(c): c for c in b}
shared = sa & sb

drift = collections.Counter()
examples: dict[str, list] = collections.defaultdict(list)
for k in shared:
    ca, cb = amap[k], bmap[k]
    if ca.get("credits") != cb.get("credits"):
        drift["credits"] += 1
        examples["credits"].append((k, ca.get("credits"), cb.get("credits")))
    if (ca.get("title") or "").strip() != (cb.get("title") or "").strip():
        drift["title"] += 1
        examples["title"].append((k, ca.get("title"), cb.get("title")))
    if ca.get("level") != cb.get("level"):
        drift["level"] += 1
        examples["level"].append((k, ca.get("level"), cb.get("level")))
    if (ca.get("preReqNotes") or "") != (cb.get("preReqNotes") or ""):
        drift["preReqNotes"] += 1

for field in ("credits", "title", "level", "preReqNotes"):
    n = drift[field]
    print(f"    {field:14} differs on {n:>5,} / {len(shared):,} shared courses "
          f"({100*n/max(len(shared),1):4.1f}%)")

for field in ("credits", "title", "level"):
    for k, va, vb in examples[field][:3]:
        cs = f"{k[0]}:{k[1]}:{k[2]}"
        print(f"      {field}: {cs:14} {A_LABEL}={va!r}  {B_LABEL}={vb!r}")

print("\n  -> course-level attributes DO drift between terms. The loader's")
print("     'update mutable fields, never the natural key' rule is what keeps")
print("     this from creating duplicate courses.")

# ------------------------------------------------------------- offerings
print()
print("=" * 78)
print("OFFERING IDENTITY  (course + term + campus)")
print("=" * 78)
oa = collections.Counter(offering_key(c, "20269") for c in a)
ob = collections.Counter(offering_key(c, "20261") for c in b)
print(f"  {A_LABEL:12} offerings={len(oa):>6,}  collisions={sum(1 for v in oa.values() if v>1)}")
print(f"  {B_LABEL:12} offerings={len(ob):>6,}  collisions={sum(1 for v in ob.values() if v>1)}")
print(f"  overlap between terms: {len(set(oa) & set(ob))}  (must be 0 - term is in the key)")

camp_a = collections.Counter(c.get("campusCode") for c in a)
camp_b = collections.Counter(c.get("campusCode") for c in b)
print(f"\n  {A_LABEL} campuses:   {dict(camp_a)}")
print(f"  {B_LABEL} campuses: {dict(camp_b)}")

# -------------------------------------------------------------- sections
print()
print("=" * 78)
print("SECTION STRUCTURE COMPARISON")
print("=" * 78)


def section_stats(data: list[dict], label: str) -> dict:
    secs = [s for c in data for s in (c.get("sections") or [])]
    meetings = [m for s in secs for m in (s.get("meetingTimes") or [])]
    tba = sum(1 for m in meetings if not (m.get("meetingDay") or "").strip())
    multi_campus = 0
    for s in secs:
        names = {
            (m.get("campusName") or "").strip()
            for m in (s.get("meetingTimes") or [])
            if (m.get("campusName") or "").strip()
        }
        if len(names) > 1:
            multi_campus += 1
    return {
        "sections": len(secs),
        "open": sum(1 for s in secs if s.get("openStatus")),
        "closed": sum(1 for s in secs if not s.get("openStatus")),
        "meetings": len(meetings),
        "tba_meetings": tba,
        "instructors": sum(len(s.get("instructors") or []) for s in secs),
        "no_instructor": sum(1 for s in secs if not s.get("instructors")),
        "xlist": sum(1 for s in secs if s.get("crossListedSections")),
        "multi_campus": multi_campus,
        "max_meetings": max((len(s.get("meetingTimes") or []) for s in secs), default=0),
        "max_instructors": max((len(s.get("instructors") or []) for s in secs), default=0),
    }


sta, stb = section_stats(a, A_LABEL), section_stats(b, B_LABEL)
print(f"  {'metric':22} {A_LABEL:>14} {B_LABEL:>14}   delta")
print("  " + "-" * 66)
for k in sta:
    va, vb = sta[k], stb[k]
    if k in ("sections", "meetings", "instructors"):
        d = f"{100*(vb-va)/max(va,1):+.1f}%"
    else:
        d = f"{vb-va:+,}"
    print(f"  {k:22} {va:>14,} {vb:>14,}   {d}")

print(f"\n  TBA meeting rate:     {A_LABEL} {100*sta['tba_meetings']/max(sta['meetings'],1):.1f}%"
      f"   {B_LABEL} {100*stb['tba_meetings']/max(stb['meetings'],1):.1f}%")
print(f"  open rate:            {A_LABEL} {100*sta['open']/max(sta['sections'],1):.1f}%"
      f"   {B_LABEL} {100*stb['open']/max(stb['sections'],1):.1f}%")

# Structural keys present in one term but not the other.
keys_a = set().union(*(set(s.keys()) for c in a for s in (c.get("sections") or [])))
keys_b = set().union(*(set(s.keys()) for c in b for s in (c.get("sections") or [])))
print(f"\n  section keys only in {A_LABEL}:   {sorted(keys_a - keys_b) or 'none'}")
print(f"  section keys only in {B_LABEL}: {sorted(keys_b - keys_a) or 'none'}")

# Would any Spring section fail our validators' shape rules?
import re

bad_index = [s["index"] for c in b for s in (c.get("sections") or [])
             if not re.fullmatch(r"[0-9]+", s.get("index") or "")]
print(f"\n  {B_LABEL} sections with non-numeric index: {len(bad_index)} {bad_index[:5]}")

bad_day = collections.Counter(
    m.get("meetingDay") for c in b for s in (c.get("sections") or [])
    for m in (s.get("meetingTimes") or [])
    if (m.get("meetingDay") or "").strip() and m.get("meetingDay") not in set("MTWHFSU")
)
print(f"  {B_LABEL} meetings with unknown meetingDay: {dict(bad_day) or 'none'}")

end_le_start = sum(
    1 for c in b for s in (c.get("sections") or []) for m in (s.get("meetingTimes") or [])
    if (m.get("startTimeMilitary") or "").isdigit()
    and (m.get("endTimeMilitary") or "").isdigit()
    and m["endTimeMilitary"] <= m["startTimeMilitary"]
)
print(f"  {B_LABEL} meetings with end <= start: {end_le_start}")
