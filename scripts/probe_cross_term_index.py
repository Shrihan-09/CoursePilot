"""Phase 2.5: does Rutgers reuse registration indexes across terms?

This is the PRIMARY question behind the (term_code, index_number) identity
decision, and it can be answered by measurement alone - no ingestion, no
schema change, no database.

Spring 2027 is not published yet, so this uses the terms that DO return data.
Answering the question with real multi-term payloads is strictly better
evidence than a synthetic test, and it costs nothing structural.

Archives each term so re-runs are offline.
"""

from __future__ import annotations

import collections
import hashlib
import json
import pathlib
import time

import httpx

UA = "CoursePilot/0.1 (Rutgers student academic planning project)"
BASE = "https://classes.rutgers.edu/soc/api/courses.json"
ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

TERM_NAME = {"0": "Winter", "1": "Spring", "7": "Summer", "9": "Fall"}
TERMS = [(2025, "9"), (2026, "1"), (2026, "7"), (2026, "9"), (2027, "0")]


def load(year: int, term: str, campus: str = "NB") -> list[dict]:
    archive = RAW / f"soc_courses_{year}_{term}_{campus}.json"
    if archive.exists():
        print(f"  {year}-{term}: cached ({archive.stat().st_size:,} bytes)")
        return json.loads(archive.read_text(encoding="utf-8"))

    url = f"{BASE}?year={year}&term={term}&campus={campus}"
    with httpx.Client(timeout=300.0, headers={"User-Agent": UA}, follow_redirects=True) as c:
        r = c.get(url)
    r.raise_for_status()
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(r.content)
    digest = hashlib.sha256(r.content).hexdigest()
    print(f"  {year}-{term}: fetched {len(r.content):,} bytes, sha256={digest[:12]}...")
    time.sleep(2)
    return r.json()


print("loading terms")
payloads: dict[tuple[int, str], list[dict]] = {}
for y, t in TERMS:
    payloads[(y, t)] = load(y, t)

# ---------------------------------------------------------------- per term
print("\n" + "=" * 78)
print("PER-TERM SECTION INDEX UNIQUENESS")
print("=" * 78)
index_sets: dict[tuple[int, str], set[str]] = {}
for key, data in payloads.items():
    y, t = key
    idx = [s["index"] for c in data for s in (c.get("sections") or [])]
    ctr = collections.Counter(idx)
    dupes = {k: v for k, v in ctr.items() if v > 1}
    index_sets[key] = set(idx)
    label = f"{y} {TERM_NAME[t]}"
    print(
        f"  {label:14} sections={len(idx):>6,}  distinct={len(ctr):>6,}  "
        f"duplicates_within_term={len(dupes)}"
    )
    if dupes:
        print(f"    !! {list(dupes.items())[:5]}")

# ------------------------------------------------------------- cross-term
print("\n" + "=" * 78)
print("CROSS-TERM INDEX REUSE  (does the same index appear in two terms?)")
print("=" * 78)
keys = list(index_sets)
overlap_found = False
for i in range(len(keys)):
    for j in range(i + 1, len(keys)):
        a, b = keys[i], keys[j]
        shared = index_sets[a] & index_sets[b]
        la = f"{a[0]} {TERM_NAME[a[1]]}"
        lb = f"{b[0]} {TERM_NAME[b[1]]}"
        pct_a = 100 * len(shared) / max(len(index_sets[a]), 1)
        marker = ""
        if shared:
            overlap_found = True
            marker = "  <-- REUSED"
        print(f"  {la:14} vs {lb:14} shared={len(shared):>6,} ({pct_a:5.1f}% of first){marker}")

print()
if overlap_found:
    print("  VERDICT: Rutgers DOES reuse registration indexes across terms.")
    print("  -> index_number alone is NOT a safe key. (term_code, index_number)")
    print("     is REQUIRED, and the Phase 2 decision is vindicated by real data.")
else:
    print("  VERDICT: no index reuse observed across the sampled terms.")

# ------------------------------------- what does a reused index point at?
print("\n" + "=" * 78)
print("EVIDENCE: reused indexes point at DIFFERENT courses")
print("=" * 78)


def index_map(key: tuple[int, str]) -> dict[str, tuple[str, str, str]]:
    out = {}
    for c in payloads[key]:
        for s in c.get("sections") or []:
            out[s["index"]] = (
                c["courseString"],
                (c.get("supplementCode") or "").strip(),
                s.get("number", "?"),
            )
    return out


fall26 = (2026, "9")
shown = 0
for other in keys:
    if other == fall26:
        continue
    shared = index_sets[fall26] & index_sets[other]
    if not shared:
        continue
    m_a, m_b = index_map(fall26), index_map(other)
    differing = [ix for ix in shared if m_a[ix][0] != m_b[ix][0]]
    same = len(shared) - len(differing)
    lb = f"{other[0]} {TERM_NAME[other[1]]}"
    print(f"\n  Fall 2026 vs {lb}: {len(shared):,} shared indexes")
    print(f"    point at a DIFFERENT course: {len(differing):,}")
    print(f"    point at the SAME course:    {same:,}")
    for ix in sorted(differing)[:5]:
        print(f"      index {ix}: Fall2026={m_a[ix][0]}:{m_a[ix][2]}  {lb}={m_b[ix][0]}:{m_b[ix][2]}")
    shown += 1
    if shown >= 3:
        break

# --------------------------------------------------- live drift vs archive
print("\n" + "=" * 78)
print("SOURCE VOLATILITY: live Fall 2026 vs the archive ingested on 2026-09-08")
print("=" * 78)
# The (2026,'9') entry above is the ARCHIVE (what the database holds), so a
# fresh fetch is needed to see drift. Measured by probe_term_availability.py
# on 2026-09-12; re-stated here rather than re-downloading 21 MB.
print("  archive (2026-09-08, ingested): 4,400 courses / 11,992 sections")
print("  live    (2026-09-12, measured): 4,396 courses / 12,004 sections")
print("  -> the published term is STILL CHANGING after classes begin.")
print("     This is why provenance carries a content hash and a retrieval")
print("     timestamp: the payload is a point-in-time observation, not a fact.")
