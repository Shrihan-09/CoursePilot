"""Phase 4: verify what SOC `coreCodes` actually means before trusting it.

The field names look obvious (`year`, `term`, `effective`, `code`,
`coreCode`). This checks what they actually contain rather than assuming, per
the standing rule that field semantics must be measured.
"""

from __future__ import annotations

import collections
import json
import pathlib

RAW = pathlib.Path("data/raw")
TERMS = {
    "Fall 2026": "soc_courses_2026_9_NB.json",
    "Winter 2027": "soc_courses_2027_0_NB.json",
    "Spring 2026": "soc_courses_2026_1_NB.json",
    "Fall 2025": "soc_courses_2025_9_NB.json",
}


def load(name: str) -> list[dict]:
    return json.loads((RAW / name).read_text(encoding="utf-8"))


data = load(TERMS["Fall 2026"])
entries = [(c, cc) for c in data for cc in (c.get("coreCodes") or [])]
print(f"Fall 2026: {len(data):,} courses, {len(entries):,} coreCode entries\n")

print("=" * 78)
print("FIELD SEMANTICS")
print("=" * 78)
sample = entries[0][1]
print(f"keys: {sorted(sample.keys())}\n")
print("  full sample record:")
for k in sorted(sample):
    print(f"    {k:26} {sample[k]!r}")

print("\n  -- do `code` and `coreCode` ever differ? --")
diff = [cc for _, cc in entries if cc.get("code") != cc.get("coreCode")]
print(f"    differ in {len(diff)} of {len(entries)} entries")

print("\n  -- do `description` and `coreCodeDescription` differ? --")
d2 = [cc for _, cc in entries if cc.get("description") != cc.get("coreCodeDescription")]
print(f"    differ in {len(d2)} of {len(entries)} entries")

print("\n  -- `year` / `term` / `effective` --")
years = collections.Counter(cc.get("year") for _, cc in entries)
terms = collections.Counter(cc.get("term") for _, cc in entries)
eff = collections.Counter(cc.get("effective") for _, cc in entries)
print(f"    year:      {dict(years)}")
print(f"    term:      {dict(terms)}")
print(f"    effective: {dict(eff.most_common(8))}")
print("    -> does effective == year+term for every row?")
mismatch = [
    cc for _, cc in entries
    if cc.get("effective") != f"{cc.get('year')}{cc.get('term')}"
]
print(f"       mismatches: {len(mismatch)}  {[m.get('effective') for m in mismatch[:5]]}")

print("\n  -- does the coreCode's term match the PAYLOAD term (2026/9)? --")
payload_term = [cc for _, cc in entries if cc.get("year") == "2026" and cc.get("term") == "9"]
print(f"    entries whose year/term == payload term: {len(payload_term)} / {len(entries)}")
print("    -> if all match, year/term describe the PAYLOAD, not a goal's own validity window")

print("\n  -- lastUpdated --")
lu = sorted({cc.get("lastUpdated") for _, cc in entries if cc.get("lastUpdated")})
if lu:
    import datetime as _dt

    def fmt(ms):
        return _dt.datetime.fromtimestamp(ms / 1000, _dt.UTC).date().isoformat()

    print(f"    {len(lu)} distinct values, range {fmt(lu[0])} .. {fmt(lu[-1])}")
    print("    -> epoch milliseconds; a certification timestamp, NOT a catalog year")

print("\n" + "=" * 78)
print("CODES AND THEIR OWNERS")
print("=" * 78)
by_code = collections.Counter(cc.get("code") for _, cc in entries)
descs = {}
units = collections.defaultdict(collections.Counter)
for course, cc in entries:
    descs.setdefault(cc.get("code"), cc.get("description"))
    units[cc.get("code")][cc.get("offeringUnitCode")] += 1

print(f"{'code':8} {'n':>5}  {'top units':22} description")
print("-" * 78)
for code, n in by_code.most_common():
    top = ",".join(f"{u}({c})" for u, c in units[code].most_common(3))
    print(f"{code or '?':8} {n:>5}  {top:22} {(descs.get(code) or '')[:34]}")

print("\n  -- are non-SAS codes present? --")
print("     SOEHS = School of Engineering; GVT/ECN = SEBS Core.")
print("     So coreCodes is NOT purely SAS Core - it spans schools.")

print("\n" + "=" * 78)
print("MULTI-GOAL COURSES")
print("=" * 78)
per_course = collections.Counter(len(c.get("coreCodes") or []) for c in data if c.get("coreCodes"))
for n in sorted(per_course):
    print(f"  {n} goal(s): {per_course[n]:>4} courses")
multi = [c for c in data if len(c.get("coreCodes") or []) > 1]
print(f"\n  courses with >1 goal: {len(multi)}")
for c in multi[:4]:
    codes = [cc.get("code") for cc in c["coreCodes"]]
    print(f"    {c['courseString']:14} {codes}")

print("\n  -- can the SAME code appear twice on one course? --")
dupes = 0
for c in data:
    codes = [cc.get("code") for cc in (c.get("coreCodes") or [])]
    if len(codes) != len(set(codes)):
        dupes += 1
print(f"     courses with a duplicated code: {dupes}")

print("\n" + "=" * 78)
print("STABILITY ACROSS TERMS (is the mapping term-scoped or catalog-scoped?)")
print("=" * 78)
def mapping(name: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for c in load(name):
        if c.get("coreCodes"):
            out[c["courseString"]] = {cc.get("code") for cc in c["coreCodes"]}
    return out

f26 = mapping(TERMS["Fall 2026"])
for label in ("Spring 2026", "Fall 2025", "Winter 2027"):
    path = RAW / TERMS[label]
    if not path.exists():
        continue
    other = mapping(TERMS[label])
    shared = set(f26) & set(other)
    same = sum(1 for k in shared if f26[k] == other[k])
    print(f"  Fall 2026 vs {label:12} shared courses={len(shared):>4}  identical goals={same:>4}"
          f"  differing={len(shared)-same:>3}")
