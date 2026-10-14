"""Build the Phase 6.7 availability fixture from an archived openSections.json payload.

The real Fall 2026 payload fetched 2026-10-14T02:29:51Z (12,001 indexes, all
campuses - the `campus` parameter is ignored), FILTERED to:

  * every index of the Fall 2026 sections in the component and schedule
    fixtures that Rutgers listed as open at that moment (unlisted = closed);
  * the first 25 listed indexes that are NOT New Brunswick Fall 2026 sections
    (other campuses), so the "ignored index" path runs on real values.

Plus three real payloads for OTHER terms, fetched the same minute, for the
wrong-term guards: Summer 2026 and Winter 2027 whole (they share no index
with the Fall fixture), and Spring 2026 filtered to the indexes it SHARES
with the Fall component fixture (indexes are reused across terms).

Entries are copied verbatim and in their published order; nothing is added
or edited. Tests that need a state Rutgers did not publish change a COPY and
label it SYNTHETIC.

Usage:  python scripts/make_availability_fixture.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = ROOT / "data" / "raw" / "live"
OUT = ROOT / "ingestion" / "tests" / "fixtures"

PAYLOAD = LIVE / "openSections_2026_9_NB_20261014T022951Z.json"
NB_COURSES = LIVE / "courses_2026_9_NB_20261014T023008Z.json"
FIXTURES = ["soc_components_fall2026_sample.json", "soc_schedule_fall2026_sample.json"]
OTHER_CAMPUS_SAMPLE = 25


def _indexes(path: pathlib.Path) -> set[str]:
    return {s["index"] for c in json.loads(path.read_bytes()) for s in c["sections"]}


def main() -> None:
    published = json.loads(PAYLOAD.read_bytes())
    wanted = set().union(*(_indexes(OUT / f) for f in FIXTURES))
    nb = _indexes(NB_COURSES)
    others = [i for i in published if i not in nb][:OTHER_CAMPUS_SAMPLE]
    keep = wanted | set(others)
    out = [i for i in published if i in keep]
    target = OUT / "open_sections_fall2026_20261014T022951Z.json"
    target.write_text(json.dumps(out), encoding="utf-8")
    print(f"{target.name}: {len(out)} of {len(published)} entries "
          f"({len(out) - len(others)} fixture sections open, {len(others)} other-campus)")

    components = _indexes(OUT / FIXTURES[0])
    for name, keep_only in (("openSections_2026_7_NB_20261014T022957Z.json", None),
                            ("openSections_2027_0_NB_20261014T023001Z.json", None),
                            ("openSections_2026_1_NB_20261014T022959Z.json", components)):
        entries = json.loads((LIVE / name).read_bytes())
        kept = [i for i in entries if keep_only is None or i in keep_only]
        term = name.split("_")[1] + "_" + name.split("_")[2]
        target = OUT / f"open_sections_other_term_{term}.json"
        target.write_text(json.dumps(kept), encoding="utf-8")
        print(f"{target.name}: {len(kept)} of {len(entries)} entries")


if __name__ == "__main__":
    main()
