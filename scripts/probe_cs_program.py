"""Phase 3: extract the CS major page from the Rutgers (Coursedog) catalog.

Nuxt 3 serializes its SSR payload as a FLAT array where every nested value is
an integer INDEX into that same array. This resolves those pointers back into
real JSON so we can see what the catalog actually publishes for a program.

Decisive question: does the catalog expose STRUCTURED requirement data
(choose-N-from-M, credit minimums, nested groups), or only prose HTML?
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
NUXT = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

PAGES = {
    "cs_26-27": (
        "https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu"
        "/schools/sas/program-listing/computer-science-198"
    ),
    "cs_25-26": (
        "https://newbrunswick-25-26-undergrad-archive.catalogs.rutgers.edu"
        "/schools/sas/program-listing/computer-science-198"
    ),
}


def fetch(url: str, name: str) -> str:
    RAW.mkdir(parents=True, exist_ok=True)
    cached = RAW / f"{name}.html"
    if cached.exists():
        print(f"  cached {name}.html ({cached.stat().st_size:,} bytes)")
        return cached.read_text(encoding="utf-8")
    with httpx.Client(timeout=120.0, headers={"User-Agent": UA}, follow_redirects=True) as c:
        r = c.get(url)
    print(f"  {name}: HTTP {r.status_code}, {len(r.text):,} chars")
    r.raise_for_status()
    cached.write_text(r.text, encoding="utf-8")
    return r.text


def resolve(flat: list, idx: int, depth: int = 0, seen: set | None = None):
    """Resolve Nuxt's pointer-array into plain JSON, with cycle protection."""
    if depth > 12:
        return "<max-depth>"
    seen = seen or set()
    if not isinstance(idx, int) or idx < 0 or idx >= len(flat):
        return idx
    if idx in seen:
        return "<cycle>"
    node = flat[idx]
    if isinstance(node, (str, int, float, bool)) or node is None:
        return node
    seen = seen | {idx}
    if isinstance(node, list):
        return [resolve(flat, i, depth + 1, seen) for i in node]
    if isinstance(node, dict):
        return {k: resolve(flat, v, depth + 1, seen) for k, v in node.items()}
    return node


def analyse(html: str, label: str) -> None:
    m = NUXT.search(html)
    if not m:
        print(f"  {label}: no __NUXT_DATA__")
        return
    flat = json.loads(m.group(1))
    strings = [x for x in flat if isinstance(x, str)]

    print(f"\n  --- {label}: {len(flat):,} nodes, {len(strings):,} strings")

    # Course codes anywhere in the payload?
    codes = sorted({s for s in strings if re.fullmatch(r"\d{2}:\d{3}:\d{3}", s)})
    print(f"      bare course codes (NN:NNN:NNN): {len(codes)}  {codes[:8]}")

    # Course codes embedded inside HTML/prose blocks.
    embedded: set[str] = set()
    for s in strings:
        embedded.update(re.findall(r"\b\d{2}:\d{3}:\d{3}\b", s))
    print(f"      course codes inside text blocks: {len(embedded)}  {sorted(embedded)[:8]}")

    # Requirement-shaped keys actually present.
    req_keys = sorted(
        {
            s
            for s in strings
            if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{2,30}", s)
            and any(
                t in s.lower()
                for t in ("requirement", "rule", "credit", "coursecount", "select", "group")
            )
        }
    )
    print(f"      requirement-ish keys: {req_keys[:20]}")

    # Biggest text blocks: is the requirement content prose HTML?
    big = sorted((s for s in strings if len(s) > 300), key=len, reverse=True)
    print(f"      text blocks >300 chars: {len(big)}")
    for s in big[:2]:
        looks_html = "<" in s and ">" in s
        print(f"        [{len(s):,} chars, html={looks_html}] {s[:180]!r}")

    # Save the resolved root for manual inspection.
    out = RAW / f"{label}_resolved.json"
    try:
        out.write_text(json.dumps(resolve(flat, 0), indent=1)[:400000], encoding="utf-8")
        print(f"      resolved root -> {out.name}")
    except Exception as exc:  # noqa: BLE001
        print(f"      resolve failed: {exc}")


if __name__ == "__main__":
    for name, url in PAGES.items():
        print(f"\n{'='*70}\n{name}\n{'='*70}")
        try:
            analyse(fetch(url, name), name)
        except httpx.HTTPStatusError as exc:
            print(f"  FAILED: {exc}")
    sys.stdout.flush()
