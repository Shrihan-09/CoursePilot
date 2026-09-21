"""Probe the SOC courses.json endpoint and report its real structure.

Caches the raw payload to data/raw/ on first run, then analyzes offline. This
follows the ingestion rule "persist the raw payload before parsing" and avoids
re-downloading 21 MB from Rutgers on every run.
"""

from __future__ import annotations

import collections
import json
import pathlib

import httpx

UA = "CoursePilot/0.1 (student academic planning project; investigation probe)"
URL = "https://classes.rutgers.edu/soc/api/courses.json?year=2026&term=9&campus=NB"

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "raw" / "soc_courses_2026_9_NB.json"


def load() -> list[dict]:
    if CACHE.exists():
        print(f"using cached payload: {CACHE} ({CACHE.stat().st_size:,} bytes)")
        return json.loads(CACHE.read_text(encoding="utf-8"))

    print("fetching from Rutgers (one request)...")
    with httpx.Client(timeout=180.0, headers={"User-Agent": UA}, follow_redirects=True) as c:
        r = c.get(URL)
    r.raise_for_status()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(r.content)
    print(f"cached -> {CACHE} ({len(r.content):,} bytes)")
    return r.json()


data = load()
print(f"courses: {len(data):,}\n")


def frac(name: str, pred) -> None:
    n = sum(1 for c in data if pred(c))
    print(f"  {name:26} {n:>6,} / {len(data):,}  ({100*n/len(data):.1f}%)")


print("field population:")
frac("courseDescription", lambda c: (c.get("courseDescription") or "").strip())
frac("preReqNotes", lambda c: (c.get("preReqNotes") or "").strip())
frac("expandedTitle", lambda c: (c.get("expandedTitle") or "").strip())
frac("title", lambda c: (c.get("title") or "").strip())
frac("synopsisUrl", lambda c: (c.get("synopsisUrl") or "").strip())
frac("courseNotes", lambda c: (c.get("courseNotes") or "").strip())
frac("has sections", lambda c: c.get("sections"))
frac("credits is None", lambda c: c.get("credits") is None)
frac("coreCodes non-empty", lambda c: c.get("coreCodes"))

print("\ncourseString format check:")
bad = [c["courseString"] for c in data if len((c.get("courseString") or "").split(":")) != 3]
print(f"  not unit:subject:course   {len(bad)}")
print(f"  sample                    {[c['courseString'] for c in data[:5]]}")

print("\ncourseString uniqueness:")
counts = collections.Counter(c["courseString"] for c in data)
dupes = [k for k, v in counts.items() if v > 1]
print(f"  distinct courseStrings    {len(counts):,}")
print(f"  duplicated courseStrings  {len(dupes)}  {dupes[:5]}")

print("\ncredits types:")
for t, n in collections.Counter(type(c.get("credits")).__name__ for c in data).most_common():
    print(f"  {t:10} {n:,}")
vals = sorted({c["credits"] for c in data if isinstance(c.get("credits"), (int, float))})
print(f"  distinct numeric credit values: {vals}")

print("\nlevel values:")
for t, n in collections.Counter(c.get("level") for c in data).most_common():
    print(f"  {t!r:10} {n:,}")

print("\nsample preReqNotes (first 3 non-empty):")
shown = 0
for c in data:
    p = (c.get("preReqNotes") or "").strip()
    if p:
        print(f"  {c['courseString']}: {p[:150]}")
        shown += 1
        if shown == 3:
            break
