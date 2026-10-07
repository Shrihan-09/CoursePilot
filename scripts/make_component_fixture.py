"""Build the Phase 6.6.1 linked-component test fixtures from archived SOC payloads.

Whole, unmodified Rutgers course records (every section, meeting, note and
restriction list as published) for the patterns documented in
docs/investigations/phase-6-6-1-linked-component-semantics.md. Tests that
need a rule the archive does not contain change a COPY and label it
SYNTHETIC.

Usage:  python scripts/make_component_fixture.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "ingestion" / "tests" / "fixtures"

FALL_2026 = [
    # registration component: 0-credit LB record ("MUST REGISTER BOTH LEC/REC & LAB")
    "01:750:193", "01:750:194",
    # one index, several meetings: LEC+RECIT (physics), LEC+WORKSHOP (biology)
    "01:750:203", "01:119:115", "01:119:116",
    # academic co-requisite, separate positive-credit lab course
    # ("01:750:203 IS A CO-REQUISITE"; "01:750:227 IS A CO-REQUSITE" on 26 of 27 sections;
    #  "CO-REQ: 119:116" on 2 of 27 sections)
    "01:750:205", "01:750:227", "01:750:229", "01:119:117",
    # separate lab course with no co-requisite at all
    "01:160:161", "01:160:171",
    # prerequisite history for the requests above
    "01:750:271", "01:640:151", "01:640:152", "01:160:307",
]
FALL_2025 = [
    # LB companion whose "both" note is on the BASE record only
    "01:750:202",
    # cross-course registration link, wrap-damaged ("MU ST ALSO REGISTER FOR 01:078:117")
    "01:617:201", "01:078:117",
]
SUMMER_2026 = [
    # recitation time only in prose; note pairs index 00557 with 01:160:314 H1/H2
    "01:160:308", "01:160:314", "01:160:313",
    # workshop prose "MW" vs structured W only; separate session dates
    "01:119:115",
]


def extract(source: str, keys: list[str], target: str) -> None:
    data = json.loads((RAW / source).read_bytes())
    wanted = set(keys)
    records = [c for c in data if c["courseString"] in wanted]
    missing = sorted(wanted - {c["courseString"] for c in records})
    if missing:
        raise SystemExit(f"{source}: not found {missing}")
    payload = json.dumps(records, sort_keys=True, separators=(",", ":"))
    (OUT / target).write_text(payload, encoding="utf-8")
    sections = sum(len(c["sections"]) for c in records)
    print(f"{target}: {len(records)} course records, {sections} sections")


if __name__ == "__main__":
    extract("soc_courses_2026_9_NB.json", FALL_2026, "soc_components_fall2026_sample.json")
    extract("soc_courses_2025_9_NB.json", FALL_2025, "soc_components_fall2025_sample.json")
    extract("soc_courses_2026_7_NB.json", SUMMER_2026, "soc_components_summer2026_sample.json")
