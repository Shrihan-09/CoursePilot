"""Follow-up section probe: offering linkage, campus, and edge cases."""

from __future__ import annotations

import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
data = json.loads((ROOT / "data" / "raw" / "soc_courses_2026_9_NB.json").read_text(encoding="utf-8"))
sections = [(c, s) for c in data for s in (c.get("sections") or [])]

print("=" * 78)
print("SECTION CAMPUS vs PARENT COURSE CAMPUS")
print("=" * 78)
mismatch = [(c, s) for c, s in sections if s.get("campusCode") != c.get("campusCode")]
print(f"  sections whose campusCode != parent course campusCode: {len(mismatch):,}")
for c, s in mismatch[:5]:
    print(f"    {c['courseString']} course={c.get('campusCode')} section={s.get('campusCode')} idx={s['index']}")

print(f"\n  section campusCode values: {dict(collections.Counter(s.get('campusCode') for _, s in sections))}")

print()
print("=" * 78)
print("THE NB/OB DUPLICATE COURSE PAIR (16:400:513) - do sections differ?")
print("=" * 78)
for c in [c for c in data if c["courseString"] == "16:400:513"]:
    idxs = [s["index"] for s in c.get("sections") or []]
    print(f"  campus={c['campusCode']!r} sections={idxs}")

print()
print("=" * 78)
print("THE LECTURE/LAB PAIR (01:750:193) - supplement + sections")
print("=" * 78)
for c in [c for c in data if c["courseString"] == "01:750:193"]:
    secs = c.get("sections") or []
    print(f"  supplement={c['supplementCode']!r} count={len(secs)} numbers={[s['number'] for s in secs][:6]}")
    print(f"      indexes={[s['index'] for s in secs][:6]}")
    modes = collections.Counter(
        m.get("meetingModeDesc") for s in secs for m in (s.get("meetingTimes") or [])
    )
    print(f"      meeting modes={dict(modes)}")

print()
print("=" * 78)
print("SECTION NUMBER FORMAT")
print("=" * 78)
nums = collections.Counter(s.get("number") for _, s in sections)
lens = collections.Counter(len(s.get("number") or "") for _, s in sections)
print(f"  distinct section numbers: {len(nums)}  length distribution: {dict(lens)}")
nonnum = sorted({n for n in nums if n and not n.isdigit()})
print(f"  non-numeric section numbers: {nonnum[:20]}  (total {len(nonnum)})")

print()
print("=" * 78)
print("INDEX FORMAT")
print("=" * 78)
ilens = collections.Counter(len(s["index"]) for _, s in sections)
print(f"  index length distribution: {dict(ilens)}")
noni = sorted({s['index'] for _, s in sections if not s["index"].isdigit()})
print(f"  non-numeric indexes: {noni[:10]} (total {len(noni)})")

print()
print("=" * 78)
print("MEETING TIME EDGE CASES")
print("=" * 78)
meetings = [(c, s, m) for c, s in sections for m in (s.get("meetingTimes") or [])]
weird = [
    (c["courseString"], s["index"], m.get("meetingDay"), m.get("startTimeMilitary"), m.get("endTimeMilitary"), m.get("meetingModeDesc"))
    for c, s, m in meetings
    if (m.get("startTimeMilitary") or "").isdigit()
    and (m.get("endTimeMilitary") or "").isdigit()
    and m["endTimeMilitary"] <= m["startTimeMilitary"]
]
print(f"  meetings where end <= start ({len(weird)}):")
for w in weird:
    print(f"    {w}")

# partial time data: day but no time, or time but no day
day_no_time = sum(1 for _, _, m in meetings if (m.get("meetingDay") or "").strip() and not (m.get("startTimeMilitary") or "").strip())
time_no_day = sum(1 for _, _, m in meetings if not (m.get("meetingDay") or "").strip() and (m.get("startTimeMilitary") or "").strip())
print(f"\n  day present but no time: {day_no_time}")
print(f"  time present but no day: {time_no_day}")

# start without end
s_no_e = sum(1 for _, _, m in meetings if (m.get("startTimeMilitary") or "").strip() and not (m.get("endTimeMilitary") or "").strip())
print(f"  start present but no end: {s_no_e}")

print()
print("=" * 78)
print("ONLINE / ASYNC")
print("=" * 78)
online_modes = {"ONLINE INSTRUCTION(INTERNET)"}
online_meet = sum(1 for _, _, m in meetings if m.get("meetingModeDesc") in online_modes)
print(f"  meetings with ONLINE mode: {online_meet:,}")
print(f"  distinct meetingModeCode -> desc:")
pairs = collections.Counter((m.get("meetingModeCode"), m.get("meetingModeDesc")) for _, _, m in meetings)
for (code, desc), n in sorted(pairs.items(), key=lambda kv: -kv[1]):
    print(f"    {code!r:6} {desc!r:34} {n:>6,}")

print()
print("=" * 78)
print("BUILDING / ROOM")
print("=" * 78)
b_no_r = sum(1 for _, _, m in meetings if (m.get("buildingCode") or "").strip() and not (m.get("roomNumber") or "").strip())
r_no_b = sum(1 for _, _, m in meetings if not (m.get("buildingCode") or "").strip() and (m.get("roomNumber") or "").strip())
print(f"  building without room: {b_no_r}   room without building: {r_no_b}")

print()
print("=" * 78)
print("CROSS-LISTED: does the partner index exist in THIS payload?")
print("=" * 78)
all_idx = {s["index"] for _, s in sections}
xl_refs = [
    (c["courseString"], s["index"], x.get("registrationIndex"))
    for c, s in sections
    for x in (s.get("crossListedSections") or [])
]
present = sum(1 for _, _, ri in xl_refs if ri in all_idx)
print(f"  cross-listed references: {len(xl_refs):,}; partner index present in payload: {present:,}")
print(f"  -> {len(xl_refs)-present:,} point outside this campus/term payload")
