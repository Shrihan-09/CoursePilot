"""Phase 2.5: map which Rutgers SOC terms actually return data.

Spring 2027 came back as an empty array. Before concluding "no second term is
available", establish the real availability window. This is source
investigation, not term substitution - the decision about which term to use
stays with the maintainer.

Deliberately lightweight: HEAD-like probing is not possible (the API always
returns the full body), so each request is a real fetch. Kept to a small,
ordered candidate list with a pause between calls.
"""

from __future__ import annotations

import json
import time

import httpx

UA = "CoursePilot/0.1 (Rutgers student academic planning project)"
BASE = "https://classes.rutgers.edu/soc/api/courses.json"

TERM_NAME = {"0": "Winter", "1": "Spring", "7": "Summer", "9": "Fall"}

# Ordered oldest -> newest around the known-good Fall 2026.
CANDIDATES = [
    (2025, "9"),
    (2026, "1"),
    (2026, "7"),
    (2026, "9"),  # known good - control
    (2027, "0"),
    (2027, "1"),  # the target
]


def probe(year: int, term: str) -> tuple[int, int, int]:
    """Return (bytes, courses, sections). (-1, -1, -1) on failure."""
    url = f"{BASE}?year={year}&term={term}&campus=NB"
    try:
        with httpx.Client(timeout=300.0, headers={"User-Agent": UA}, follow_redirects=True) as c:
            r = c.get(url)
    except Exception as exc:  # noqa: BLE001
        print(f"  {year} {TERM_NAME[term]:6} ERROR {type(exc).__name__}: {exc}")
        return (-1, -1, -1)

    if r.status_code != 200:
        print(f"  {year} {TERM_NAME[term]:6} HTTP {r.status_code}")
        return (-1, -1, -1)

    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        print(f"  {year} {TERM_NAME[term]:6} non-JSON ({len(r.content):,} bytes)")
        return (len(r.content), -1, -1)

    if not isinstance(data, list):
        print(f"  {year} {TERM_NAME[term]:6} unexpected type {type(data).__name__}")
        return (len(r.content), -1, -1)

    n_c = len(data)
    n_s = sum(len(c.get("sections") or []) for c in data)
    flag = "  <-- HAS DATA" if n_c else "      (empty)"
    print(f"  {year} {TERM_NAME[term]:6} term={term}  {len(r.content):>12,} bytes  "
          f"{n_c:>5,} courses  {n_s:>6,} sections{flag}")
    return (len(r.content), n_c, n_s)


if __name__ == "__main__":
    print("Rutgers SOC term availability (campus=NB)\n")
    results = {}
    for i, (y, t) in enumerate(CANDIDATES):
        results[(y, t)] = probe(y, t)
        if i < len(CANDIDATES) - 1:
            time.sleep(2)  # be polite between full-payload fetches

    print("\n" + "=" * 72)
    print("TERMS WITH DATA")
    print("=" * 72)
    have = [(y, t, r) for (y, t), r in results.items() if r[1] and r[1] > 0]
    if not have:
        print("  none")
    for y, t, r in have:
        print(f"  {y} {TERM_NAME[t]:6} (year={y} term={t}): {r[1]:,} courses, {r[2]:,} sections")
    print(json.dumps({f"{y}-{t}": r for (y, t), r in results.items()}, indent=2))
