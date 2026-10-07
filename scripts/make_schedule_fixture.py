"""Build the Phase 6.6 Schedule Engine test fixtures from archived SOC payloads.

Copies WHOLE, unmodified course records (every section, meeting, restriction
list and note exactly as Rutgers published them) for the courses named
below, from the archived NB payloads in data/raw/. Tests that need a rule the
archive does not contain change a COPY at test time and label it SYNTHETIC.

Usage:  python scripts/make_schedule_fixture.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "ingestion" / "tests" / "fixtures"

FALL_2026 = [
    # a CS student's record and requests (01:198:344 sections are open to major 198)
    "01:198:111", "01:198:112", "01:198:205", "01:198:206", "01:198:211", "01:198:334",
    "01:198:336", "01:198:344", "01:198:352", "01:198:493",
    "01:640:151", "01:640:152", "01:640:250", "01:640:300",
    # lecture + recitation under one index; lab as a separate 0-credit record
    "01:750:193", "01:750:203",
    # lab with an asynchronous online component
    "01:830:101", "01:830:302",
    # cross-listed pair (same class, two codes)
    "01:013:120", "01:074:120",
    # asynchronous-only, TBA lecture, arranged
    "01:013:143", "01:013:321",
    # restricted to another major (01:694:383: "MAJ: 694", no prerequisite);
    # restricted to SAS (unit 01)
    "01:694:383", "01:090:120",
    # two instructors; a Saturday meeting; a malformed published time
    "01:070:105", "01:090:182", "07:966:333",
    # large introductory courses
    "01:920:101", "01:070:101",
]
SUMMER_2026 = [
    # M/W 18:00-22:00 in DISJOINT sessions (Jul 6-31 vs May 26-Jul 2)
    "01:014:386", "01:202:201",
    # M/W 18:00-21:40, SAME session (Jul 6-Aug 12)
    "01:014:490", "01:202:305",
]


def extract(source: str, keys: list[str], target: str) -> None:
    data = json.loads((RAW / source).read_bytes())
    wanted = set(keys)
    records = [c for c in data if c["courseString"] in wanted]
    found = {c["courseString"] for c in records}
    missing = sorted(wanted - found)
    if missing:
        raise SystemExit(f"{source}: not found {missing}")
    payload = json.dumps(records, sort_keys=True, separators=(",", ":"))
    (OUT / target).write_text(payload, encoding="utf-8")
    sections = sum(len(c["sections"]) for c in records)
    print(f"{target}: {len(records)} course records, {sections} sections")


if __name__ == "__main__":
    extract("soc_courses_2026_9_NB.json", FALL_2026, "soc_schedule_fall2026_sample.json")
    extract("soc_courses_2026_7_NB.json", SUMMER_2026, "soc_schedule_summer2026_sample.json")
