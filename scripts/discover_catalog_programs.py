"""Phase 6.1: DISCOVERY-ONLY prototype for Rutgers catalog programs.

INVESTIGATION TOOLING - it loads nothing into any database and marks nothing
as supported. It answers: how many program pages can be found, in which
schools, with what identifiers, and how many of a sample actually contain
parseable requirement prose.

Discovery source: the site navigation tree that Coursedog embeds in EVERY
catalog page's `__NUXT_DATA__` payload (groups -> links, each link with a
label, slug, url and a Coursedog `pageId`). The catalog's own /sitemap.xml
returns 404, and school index URLs are not uniform, so the embedded tree is
the only complete enumeration observed.

Lifecycle vocabulary (never collapsed):
  DISCOVERED  a navigation leaf exists
  FETCHED     its page was retrieved and archived
  PARSED      requirement prose was located on the page
  VALIDATED   a curated definition passes loader + verbatim-quote checks
  REVIEWED    a person verified the definition against the archive
  PUBLISHED   support_status == supported
This script reaches at most PARSED, and only for a small sample.

Usage:
  python scripts/discover_catalog_programs.py            # archived pages only
  python scripts/discover_catalog_programs.py --sample 2 # + fetch 2 pages per school
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import pathlib
import re
import time
from collections import Counter, defaultdict

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "data" / "raw" / "catalog"
PROBES = ROOT / "data" / "raw" / "probes"
EVIDENCE = ROOT / "docs" / "investigations" / "evidence" / "phase-6-1-discovery.json"
UA = "CoursePilot/0.1 (Rutgers student academic planning project; investigation)"
NUXT = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
COURSE_CODE = re.compile(r"\b\d{2}:\d{3}:\d{3}\b")
# The catalog's own conventions for program pages: SAS and most schools write
# "Mathematics 640"; RBS writes "010 Accounting". Both are recognised.
SUBJECT_CODED = re.compile(r"(?:^(\d{3})\s)|(?:\s(\d{3})\s*$)")
REQUIREMENT_WORDS = re.compile(r"(major requirements|requirements for the major|"
                               r"minor requirements|degree requirements|credits)", re.I)
STRUCTURED_KEYS = ("requirementType", "courseCount", "creditsMin", "requirementGroup",
                   "ruleType", "courseList")

YEARS = {
    "2026-2027": ("catalog_computer-science-198_2026_2027.html",
                  "https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu"),
    "2025-2026": ("catalog_computer-science-198_2025_2026.html",
                  "https://newbrunswick-25-26-undergrad-archive.catalogs.rutgers.edu"),
}


def nav_tree(page_html: str) -> list[dict]:
    """Every navigation leaf, with its ancestry of group labels."""
    flat = json.loads(NUXT.search(page_html).group(1))

    def res(i):
        return flat[i] if isinstance(i, int) and 0 <= i < len(flat) else i

    def node(i):
        n = res(i)
        return {k: res(v) for k, v in n.items()} if isinstance(n, dict) else None

    groups = [n for n in (node(i) for i in range(len(flat)))
              if n and n.get("type") == "group" and str(n.get("url", "")).count("/") == 2
              and str(n.get("url", "")).startswith("/schools/")]
    leaves = []

    def walk(group: dict, trail: list[str]):
        for child_i in group.get("children") or []:
            child = node(child_i)
            if not child:
                continue
            if child.get("type") == "group":
                walk(child, [*trail, child.get("label", "")])
            elif child.get("type") == "link":
                leaves.append({
                    "school_slug": trail[0], "trail": trail[1:], "label": child.get("label"),
                    "slug": child.get("slug"), "url": child.get("url"),
                    "page_id": child.get("pageId"), "link_type": child.get("linkType"),
                })

    seen = set()
    for g in groups:
        if g["url"] in seen:
            continue
        seen.add(g["url"])
        walk(g, [g["url"].split("/")[2]])
    return leaves


def classify(leaf: dict) -> str:
    label = leaf["label"] or ""
    trail = " / ".join(leaf["trail"]).lower()
    if leaf["link_type"] != "internal":
        return "external_link"
    if SUBJECT_CODED.search(label):
        return "subject_coded_program_page"
    if any(w in trail for w in ("program", "major", "degree", "minor", "certificate")):
        return "program_area_page_uncoded"
    return "other_page"


def sample_parse(leaves_by_school, host, per_school) -> list[dict]:
    """Fetch a few subject-coded pages per school and look for requirement prose."""
    results = []
    with httpx.Client(timeout=60, headers={"User-Agent": UA}, follow_redirects=True) as c:
        for school, leaves in sorted(leaves_by_school.items()):
            coded = [l for l in leaves if l["class"] == "subject_coded_program_page"]
            for leaf in coded[:per_school]:
                cached = PROBES / f"program_{school}_{leaf['slug']}.html"
                if cached.exists():                 # never re-fetch an archived page
                    page, status = cached.read_text(encoding="utf-8"), 200
                else:
                    time.sleep(1.5)
                    r = c.get(host + leaf["url"])
                    page, status = r.text, r.status_code
                    cached.write_text(page, encoding="utf-8")
                m = NUXT.search(page)
                flat = json.loads(m.group(1)) if m else []
                strings = [s for s in flat if isinstance(s, str)]
                blocks = [s for s in strings if len(s) > 200 and "<" in s]
                text = " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", b)) for b in blocks)
                results.append({
                    "school": school, "label": leaf["label"], "status": status,
                    "prose_blocks": len(blocks),
                    "requirement_words": sorted({w.lower() for w in REQUIREMENT_WORDS.findall(text)}),
                    "course_codes_in_prose": len(set(COURSE_CODE.findall(text))),
                    # Coursedog ships FORM-SCHEMA field definitions for these
                    # keys on every page ({dataKey: "requirementType", hidden:
                    # true, label: ...}). Their presence proves the platform
                    # could hold structured requirements - not that Rutgers
                    # populated any. Populated data would be objects that USE
                    # the key, which is what is counted here.
                    "schema_mentions": [k for k in STRUCTURED_KEYS if k in strings],
                    # Two further objects on EVERY page carry these keys too,
                    # but as field-CONFIGURATION maps (courseCount -> {config},
                    # minimumGrade -> {config}, degreeMapName -> {config}):
                    # identical across unrelated programs, so platform schema.
                    # Only objects whose values are data, not config dicts,
                    # would be program content.
                    "platform_field_config_maps": sum(
                        1 for n in flat if isinstance(n, dict)
                        and any(k in n for k in STRUCTURED_KEYS)
                        and all(isinstance(flat[v] if isinstance(v, int) and v < len(flat) else v, dict)
                                for v in n.values())),
                    "populated_structured_objects": sum(
                        1 for n in flat if isinstance(n, dict)
                        and any(k in n for k in STRUCTURED_KEYS)
                        and not all(isinstance(flat[v] if isinstance(v, int) and v < len(flat) else v, dict)
                                    for v in n.values())),
                })
                print(f"  {school:15} {leaf['label'][:40]:40} {status} blocks={len(blocks)} "
                      f"codes={results[-1]['course_codes_in_prose']} "
                      f"req={bool(results[-1]['requirement_words'])} "
                      f"populated_structured={results[-1]['populated_structured_objects']}")
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    args = ap.parse_args()
    PROBES.mkdir(parents=True, exist_ok=True)
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)

    out: dict = {"years": {}}
    per_year = {}
    for year, (archive, host) in YEARS.items():
        leaves = nav_tree((ARCHIVE / archive).read_text(encoding="utf-8"))
        for leaf in leaves:
            leaf["class"] = classify(leaf)
        per_year[year] = leaves
        by_school = defaultdict(list)
        for leaf in leaves:
            by_school[leaf["school_slug"]].append(leaf)
        summary = {s: dict(Counter(l["class"] for l in ls)) for s, ls in sorted(by_school.items())}
        codes = [next(g for g in SUBJECT_CODED.search(l["label"]).groups() if g) for l in leaves
                 if l["class"] == "subject_coded_program_page"]
        out["years"][year] = {
            "navigation_leaves": len(leaves),
            "by_class": dict(Counter(l["class"] for l in leaves)),
            "by_school": summary,
            "subject_coded_pages": len(codes),
            "distinct_subject_codes": len(set(codes)),
            "duplicate_subject_codes": {c: n for c, n in Counter(codes).items() if n > 1},
            "duplicate_urls": [u for u, n in Counter(l["url"] for l in leaves).items() if n > 1][:10],
        }
        print(f"{year}: {len(leaves)} leaves  {out['years'][year]['by_class']}")
        for s, cls in summary.items():
            print(f"   {s:15} {cls}")
        if args.sample and year == "2026-2027":
            out["sample_parse"] = sample_parse(by_school, host, args.sample)

    a = {l["url"]: l for l in per_year["2026-2027"]}
    b = {l["url"]: l for l in per_year["2025-2026"]}
    same_url = a.keys() & b.keys()
    out["identifier_stability"] = {
        "urls_both_years": len(same_url),
        "urls_only_2026_2027": len(a.keys() - b.keys()),
        "urls_only_2025_2026": len(b.keys() - a.keys()),
        "same_url_same_page_id": sum(1 for u in same_url if a[u]["page_id"] == b[u]["page_id"]),
        "same_url_different_page_id": sum(1 for u in same_url if a[u]["page_id"] != b[u]["page_id"]),
        "coded_programs_both_years": len({u for u in same_url
                                          if a[u]["class"] == "subject_coded_program_page"}),
    }
    print("identifier stability:", out["identifier_stability"])
    out["leaves_2026_2027"] = [
        {k: l[k] for k in ("school_slug", "trail", "label", "url", "page_id", "class")}
        for l in per_year["2026-2027"]
    ]
    EVIDENCE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"evidence -> {EVIDENCE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
