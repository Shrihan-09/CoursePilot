"""Phase 3: investigate the Rutgers (Coursedog) catalog as a structured source.

Findings so far:
  * catalogs.rutgers.edu -> main.catalogs.rutgers.edu, one subdomain PER
    CATALOG YEAR (26-27 current, 25-26 archive) - catalog year is in the URL.
  * The site is a Nuxt 3 SPA backed by app.coursedog.com.
  * app.coursedog.com/api/v1/... returns 401 (auth required) - NOT usable.
  * The rendered pages embed a `__NUXT_DATA__` SSR payload: structured JSON
    inside the HTML, so no browser engine is required.

This script extracts that payload and reports what it actually contains.
Investigation tooling, not part of the pipeline.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import httpx

UA = "CoursePilot/0.1 (Rutgers student academic planning project)"
ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "catalog"

CATALOGS = {
    "26-27": "https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu",
    "25-26": "https://newbrunswick-25-26-undergrad-archive.catalogs.rutgers.edu",
}

NUXT_RE = re.compile(
    r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)


def fetch(url: str, cache_name: str) -> str:
    RAW.mkdir(parents=True, exist_ok=True)
    cached = RAW / cache_name
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    with httpx.Client(timeout=120.0, headers={"User-Agent": UA}, follow_redirects=True) as c:
        r = c.get(url)
    r.raise_for_status()
    cached.write_text(r.text, encoding="utf-8")
    print(f"  fetched + archived {cache_name} ({len(r.text):,} chars)")
    return r.text


def nuxt_payload(html: str) -> list | None:
    m = NUXT_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        print(f"  payload parse failed: {exc}")
        return None


def describe(payload: list, label: str) -> None:
    """Nuxt 3 serializes the payload as a FLAT array with integer pointers.
    Rather than resolve the graph, survey what strings it contains - enough to
    tell whether real catalog content is present."""
    print(f"\n--- {label}: payload is a flat array of {len(payload):,} nodes")

    strings = [x for x in payload if isinstance(x, str)]
    print(f"    strings: {len(strings):,}")

    # Look for catalog-shaped content.
    def count(pred) -> int:
        return sum(1 for s in strings if pred(s))

    print(f"    look like course codes (NN:NNN:NNN): "
          f"{count(lambda s: re.fullmatch(r'\\d{2}:\\d{3}:\\d{3}', s))}")
    print(f"    mention 'Computer Science': {count(lambda s: 'Computer Science' in s)}")
    print(f"    mention 'Core Curriculum':  {count(lambda s: 'Core Curriculum' in s)}")
    print(f"    mention 'credits':          {count(lambda s: 'credit' in s.lower())}")
    print(f"    long text blocks (>400ch):  {count(lambda s: len(s) > 400)}")

    # URL-ish slugs tell us the real routing shape.
    slugs = sorted({s for s in strings if s.startswith("/") and len(s) < 90})
    print(f"    path-like strings ({len(slugs)}): {slugs[:15]}")

    keys = sorted({s for s in strings if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{2,28}", s)})
    interesting = [
        k for k in keys
        if any(t in k.lower() for t in ("program", "requirement", "course", "catalog", "school", "page"))
    ]
    print(f"    interesting keys: {interesting[:25]}")


if __name__ == "__main__":
    for label, base in CATALOGS.items():
        print(f"\n{'='*70}\n{label}: {base}\n{'='*70}")
        html = fetch(base + "/", f"root_{label}.html")
        payload = nuxt_payload(html)
        if payload is None:
            print("  no __NUXT_DATA__ payload found")
            continue
        describe(payload, label)
    sys.stdout.flush()
