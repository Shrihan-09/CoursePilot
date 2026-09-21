"""One-off source investigation probe (Phase 1, Step 1).

Politely checks candidate Rutgers endpoints to find out what actually exists
and what shape the data has. Small number of requests, identified user agent.

This is investigation tooling, not part of the ingestion pipeline.
"""

from __future__ import annotations

import json
import sys

import httpx

UA = "CoursePilot/0.1 (student academic planning project; investigation probe)"

CANDIDATES = [
    ("subjects  sis", "https://sis.rutgers.edu/soc/api/subjects.json?semester=92026&campus=NB&level=U"),
    ("subjects  classes", "https://classes.rutgers.edu/soc/api/subjects.json?semester=92026&campus=NB&level=U"),
    ("init      classes", "https://classes.rutgers.edu/soc/api/init.json?year=2026&term=9&campus=NB"),
    ("openSect  sis", "https://sis.rutgers.edu/soc/api/openSections.json?year=2026&term=9&campus=NB"),
]


def probe(label: str, url: str) -> None:
    try:
        with httpx.Client(timeout=45.0, headers={"User-Agent": UA}, follow_redirects=True) as c:
            r = c.get(url)
    except Exception as exc:  # noqa: BLE001
        print(f"{label:18} ERROR  {type(exc).__name__}: {exc}")
        return

    ctype = r.headers.get("content-type", "?")
    size = len(r.content)
    print(f"{label:18} {r.status_code}  {size:>10,} bytes  {ctype}  -> {r.url}")

    if r.status_code == 200 and "json" in ctype:
        try:
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            print(f"{'':18} JSON parse failed: {exc}")
            return
        if isinstance(data, list):
            print(f"{'':18} list of {len(data)} items")
            if data:
                first = data[0]
                if isinstance(first, dict):
                    print(f"{'':18} first item keys: {sorted(first.keys())}")
                else:
                    print(f"{'':18} first item: {first!r}")
        elif isinstance(data, dict):
            print(f"{'':18} dict keys: {sorted(data.keys())[:40]}")


if __name__ == "__main__":
    for label, url in CANDIDATES:
        probe(label, url)
        print()
    sys.stdout.flush()
