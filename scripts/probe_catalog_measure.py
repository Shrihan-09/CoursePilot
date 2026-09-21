"""Phase 3 measurement: course descriptions + catalog-year differences.

Answers two questions with real data:

  1. Does the catalog supply the course DESCRIPTIONS that SOC lacks, and for
     how many courses?
  2. Do requirements actually differ between catalog years? (If not, catalog
     versioning still matters, but we should say so honestly.)
"""

from __future__ import annotations

import difflib
import html as htmllib
import json
import pathlib
import re

RAW = pathlib.Path("data/raw/catalog")
NUXT = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)


def blocks(name: str) -> list[str]:
    flat = json.loads(NUXT.search((RAW / f"{name}.html").read_text(encoding="utf-8")).group(1))
    return sorted((s for s in flat if isinstance(s, str) and len(s) > 300), key=len, reverse=True)


def plain(s: str) -> str:
    return htmllib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).strip()


# Course entries look like: <strong>01:198:103</strong> <strong><em>Title (credits)</em></strong>Description
COURSE_ENTRY = re.compile(
    r"<strong>\s*(\d{2}:\d{3}:\d{3})\s*</strong>\s*"          # code
    r"<strong><em>\s*(.*?)\s*\((.*?)\)\s*(?:<br\s*/?>)?\s*</em></strong>"  # title (credits)
    r"(.*?)(?=<strong>\s*\d{2}:\d{3}:\d{3}\s*</strong>|$)",   # description up to next code
    re.DOTALL,
)


def parse_courses(course_block: str) -> list[dict]:
    out = []
    for code, title, credits, desc in COURSE_ENTRY.findall(course_block):
        out.append(
            {
                "code": code,
                "title": plain(title),
                "credits_raw": plain(credits),
                "description": plain(desc),
            }
        )
    return out


print("=" * 78)
print("COURSE DESCRIPTIONS FROM THE CATALOG (CS 198 page)")
print("=" * 78)

for year in ("26-27", "25-26"):
    bs = blocks(f"cs_{year}")
    course_block = bs[0]
    courses = parse_courses(course_block)
    with_desc = [c for c in courses if len(c["description"]) > 30]
    print(f"\n  {year}: parsed {len(courses)} course entries from a {len(course_block):,}-char block")
    print(f"        with a real description (>30 chars): {len(with_desc)} "
          f"({100*len(with_desc)/max(len(courses),1):.0f}%)")
    if courses:
        c = courses[0]
        print(f"        sample: {c['code']}  {c['title']!r}  credits={c['credits_raw']!r}")
        print(f"                {c['description'][:150]}...")
        # credit formats actually observed
        fmts = sorted({c["credits_raw"] for c in courses})
        print(f"        distinct credit strings ({len(fmts)}): {fmts[:12]}")

print()
print("=" * 78)
print("CATALOG-YEAR DIFFERENCE: 25-26 vs 26-27")
print("=" * 78)

b26, b25 = blocks("cs_26-27"), blocks("cs_25-26")


def find(bs: list[str], pattern: str) -> str | None:
    for b in bs:
        if re.search(pattern, plain(b), re.I):
            return plain(b)
    return None


for label, pat in [
    ("Major Requirements", r"^Major Requirements"),
    ("Minor Requirements", r"^Minor Requirements"),
    ("Entry Requirements", r"^Entry Requirements"),
    ("Learning Goals", r"^Learning Goals"),
]:
    t26, t25 = find(b26, pat), find(b25, pat)
    if t26 is None or t25 is None:
        print(f"\n  {label}: present26={t26 is not None} present25={t25 is not None}")
        continue
    same = t26 == t25
    print(f"\n  {label}: identical={same}  (26-27 {len(t26)} chars, 25-26 {len(t25)} chars)")
    if not same:
        sm = difflib.SequenceMatcher(None, t25, t26)
        print(f"    similarity: {sm.ratio():.3f}")
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                print(f"      {tag}: 25-26={t25[i1:i2][:120]!r}")
                print(f"           26-27={t26[j1:j2][:120]!r}")

# Course description drift between years.
c26 = {c["code"]: c for c in parse_courses(b26[0])}
c25 = {c["code"]: c for c in parse_courses(b25[0])}
shared = set(c26) & set(c25)
print(f"\n  courses in both years: {len(shared)}")
print(f"  only 26-27: {sorted(set(c26)-set(c25))}")
print(f"  only 25-26: {sorted(set(c25)-set(c26))}")
diff_desc = [k for k in shared if c26[k]["description"] != c25[k]["description"]]
diff_title = [k for k in shared if c26[k]["title"] != c25[k]["title"]]
diff_cred = [k for k in shared if c26[k]["credits_raw"] != c25[k]["credits_raw"]]
print(f"  description changed: {len(diff_desc)} {diff_desc[:5]}")
print(f"  title changed:       {len(diff_title)} {diff_title[:5]}")
print(f"  credits changed:     {len(diff_cred)} {diff_cred[:5]}")
