"""Phase 2.5 Step 2: verify the Rutgers SOC source for Spring 2027.

Checks availability and structural identity against the Fall 2026 baseline
BEFORE any ingestion. If Spring 2027 is not published yet, this reports that
and nothing is ingested - no guessed substitute term.

Polite: one request per candidate term, archived on success so later runs are
offline.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import httpx

UA = "CoursePilot/0.1 (Rutgers student academic planning project)"
BASE = "https://classes.rutgers.edu/soc/api/courses.json"
ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

# Term digit is Rutgers' own: 9=Fall, 1=Spring, 7=Summer, 0=Winter.
# Only (2026, 9) has been verified by this project so far.
CANDIDATES = [
    (2027, "1", "NB"),  # Spring 2027 - the target
]

# Keys observed on every Fall 2026 course object; used to confirm the
# response shape has not changed between terms.
FALL_COURSE_KEYS = {
    "campusCode", "courseNumber", "courseString", "credits", "level",
    "offeringUnitCode", "openSections", "school", "sections", "subject",
    "supplementCode", "title",
}
FALL_SECTION_KEYS = {
    "index", "number", "openStatus", "meetingTimes", "instructors",
    "campusCode", "crossListedSections", "sectionCourseType",
}


def probe(year: int, term: str, campus: str) -> dict | None:
    url = f"{BASE}?year={year}&term={term}&campus={campus}"
    print(f"\n=== {year} term={term} campus={campus} ===")
    print(f"  url: {url}")
    try:
        with httpx.Client(timeout=300.0, headers={"User-Agent": UA}, follow_redirects=True) as c:
            r = c.get(url)
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR {type(exc).__name__}: {exc}")
        return None

    ctype = r.headers.get("content-type", "?")
    print(f"  status:       {r.status_code}")
    print(f"  content-type: {ctype}")
    print(f"  bytes:        {len(r.content):,}")
    print(f"  final url:    {r.url}")

    if r.status_code != 200 or "json" not in ctype:
        print("  -> NOT AVAILABLE as JSON")
        return None

    try:
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        print(f"  JSON parse failed: {exc}")
        return None

    if not isinstance(data, list):
        print(f"  -> unexpected top-level type: {type(data).__name__}")
        return None

    n_courses = len(data)
    n_sections = sum(len(c.get("sections") or []) for c in data)
    print(f"  courses:      {n_courses:,}")
    print(f"  sections:     {n_sections:,}")

    if n_courses == 0:
        print("  -> EMPTY: term exists in the API but has no published courses yet")
        return {"year": year, "term": term, "campus": campus, "courses": 0, "sections": 0}

    # Structural comparison against Fall 2026.
    ckeys = set(data[0].keys())
    missing_c = FALL_COURSE_KEYS - ckeys
    extra_c = ckeys - FALL_COURSE_KEYS
    print(f"  course keys missing vs Fall 2026: {sorted(missing_c) or 'none'}")
    print(f"  course keys new vs Fall 2026:     {sorted(extra_c) or 'none'}")

    sec = next((s for c in data for s in (c.get("sections") or [])), None)
    if sec:
        skeys = set(sec.keys())
        print(f"  section keys missing vs Fall 2026: {sorted(FALL_SECTION_KEYS - skeys) or 'none'}")
        print(f"  section keys new vs Fall 2026:     {sorted(skeys - FALL_SECTION_KEYS)[:8]}")

    # Confirm the campus actually returned matches what we asked for.
    campuses = {c.get("campusCode") for c in data}
    print(f"  campusCodes present: {sorted(x for x in campuses if x)}")

    archive = RAW / f"soc_courses_{year}_{term}_{campus}.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(r.content)
    digest = hashlib.sha256(r.content).hexdigest()
    print(f"  archived -> {archive.name}")
    print(f"  sha256:   {digest[:16]}...")

    return {
        "year": year,
        "term": term,
        "campus": campus,
        "courses": n_courses,
        "sections": n_sections,
        "bytes": len(r.content),
        "sha256": digest,
        "archive": str(archive),
    }


if __name__ == "__main__":
    results = [probe(*c) for c in CANDIDATES]
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for cand, res in zip(CANDIDATES, results, strict=True):
        y, t, camp = cand
        if res is None:
            print(f"  {y}-{t}-{camp}: UNAVAILABLE")
        elif res["courses"] == 0:
            print(f"  {y}-{t}-{camp}: EMPTY (0 courses)")
        else:
            print(f"  {y}-{t}-{camp}: {res['courses']:,} courses / {res['sections']:,} sections")
    sys.stdout.flush()
