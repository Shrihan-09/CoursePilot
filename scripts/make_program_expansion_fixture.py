"""Build the Prompt 08 B1 test fixture: real public SOC course records for
synthetic students in SAS, RBS, SoE and SEBS (Fall 2026 and Spring 2027).

Copies course records from the archived NB payloads in data/raw/ (read only)
for the courses named below, plus - for each Core/H&SS certification code the
school components use - the first few certified courses in each term (sorted,
so deterministic). Each record keeps every field as Rutgers published it,
with its SECTIONS TRIMMED to the first three (fixture size); nothing else is
altered. Student data never enters this file.

Usage:  python scripts/make_program_expansion_fixture.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "ingestion" / "tests" / "fixtures" / "soc_program_expansion_terms.json"
TERMS = ["2026_9", "2027_1"]
NAMED = [
    # RBS business core (fall 2026 entrants) and courses named only as prerequisites
    "33:010:272", "33:011:301", "33:011:302", "33:011:303", "33:390:300", "33:620:302",
    "33:136:385", "33:620:369", "33:630:301", "33:799:301", "33:136:386", "33:620:492",
    "33:136:370", "33:010:458", "33:522:334", "33:522:335", "33:140:320", "33:136:388",
    "01:220:102", "01:220:103", "01:640:135", "01:960:285",
    # SoE first-year program 004 (+ a sophomore course for a synthetic major)
    "01:160:159", "01:160:160", "01:160:171", "01:355:101", "01:640:151", "01:640:152",
    "01:750:123", "01:750:124", "14:440:101", "14:440:102", "14:440:221", "14:440:222",
    # a SEBS synthetic major's own courses
    "01:119:115", "01:119:116", "01:119:117",
]
CODES = ["SOEHS", "CCD", "CCO", "NS", "HST", "SCL", "ECN", "GVT", "AHo", "AHp", "WC", "WCd",
         "QQ", "QR", "XPL"]
PER_CODE = 3
MAX_SECTIONS = 3


def main() -> None:
    out = {}
    for term in TERMS:
        records = json.loads((RAW / f"soc_courses_{term}_NB.json").read_bytes())
        by_string = {}
        for r in records:
            if (r.get("supplementCode") or "").strip():
                continue
            by_string.setdefault(r["courseString"], r)
        keep = {c for c in NAMED if c in by_string}
        for code in CODES:
            certified = sorted(cs for cs, r in by_string.items()
                               if any(cc.get("code") == code for cc in r.get("coreCodes") or ()))
            keep |= set(certified[:PER_CODE])
        chosen = []
        for cs in sorted(keep):
            r = dict(by_string[cs])
            r["sections"] = list(r.get("sections") or [])[:MAX_SECTIONS]
            chosen.append(r)
        out[term] = chosen
        print(term, len(chosen), "courses;", sorted(set(NAMED) - keep), "not offered")
    OUT.write_text(json.dumps(out, indent=None, separators=(",", ":")), encoding="utf-8")
    print(OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
