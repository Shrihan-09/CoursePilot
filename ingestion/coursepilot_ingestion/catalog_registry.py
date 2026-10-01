"""Rutgers catalog discovery registry (Phase 6.3).

Deterministic, offline: everything here reads ARCHIVED catalog pages.

```
any archived catalog page ──► navigation tree ──► catalog_page rows        DISCOVERED
archived program page      ──► normalized prose ──► prose_sha256            FETCHED
                           ──► credential headings ──► program_candidate    PARSED (candidate only)
```

## Discovery source

Coursedog embeds the whole catalog navigation in every page's
`__NUXT_DATA__` payload: groups (school -> sections) and links, each link with
a label, slug, url and a stable `pageId`. The catalog's /sitemap.xml returns
404 and school index URLs are not uniform (Phase 6.1), so this embedded tree
is the only complete enumeration observed.

## A page is not a program

One page defines zero or more credentials. The Mathematics page defines a
major, three options, two interdisciplinary majors, a minor and a
certificate. Candidates are extracted from the page's own headings - `<h3>`
sections and `<p><strong>` sub-headings - and are CLAIMS: nothing here
creates a Program or makes anything student-facing.

## Content identity

`prose_sha256` hashes the page's normalized prose (text of its content
blocks, markup stripped, whitespace collapsed). The raw HTML also carries
Coursedog build hashes and serialized state that change without any
requirement changing; hashing prose means a source-change check reacts to
what Rutgers SAYS, not to how the page was rendered.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.models import CatalogPage, ProgramCandidate
from sqlalchemy import select
from sqlalchemy.orm import Session

NUXT = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
#: "Mathematics 640" (SAS and most schools) or "010 Accounting" (RBS).
SUBJECT_CODED = re.compile(r"(?:^(\d{3})\s)|(?:\s(\d{3})\s*$)")
PROGRAM_TRAIL_WORDS = ("program", "major", "degree", "minor", "certificate")
#: Markers of non-content blocks in the payload (analytics, theme CSS).
_NOT_PROSE = ("dataLayer", "#main-content", "gtag(")
#: v2 (Phase 6.3 wave): any heading containing "certificate", and
#: "Bachelor of ..." headings. Known v2 gap: majors introduced by PLAIN
#: paragraphs (Statistics page: "Statistics", "Statistics/Mathematics") and
#: track lists (Data Science) are not detected.
EXTRACTOR_VERSION = "catalog-headings/2"


@dataclass(frozen=True, slots=True)
class NavLeaf:
    school_slug: str
    trail: tuple[str, ...]
    title: str
    url_path: str
    source_page_id: str | None
    link_type: str | None

    @property
    def subject_code(self) -> str | None:
        m = SUBJECT_CODED.search(self.title or "")
        return next((g for g in m.groups() if g), None) if m else None

    @property
    def page_class(self) -> str:
        if self.link_type != "internal":
            return "external"
        if self.subject_code:
            return "subject_coded"
        if any(w in " / ".join(self.trail).lower() for w in PROGRAM_TRAIL_WORDS):
            return "program_area"
        return "other"


def _flat(page_html: str) -> list:
    m = NUXT.search(page_html)
    if not m:
        raise ValueError("page has no __NUXT_DATA__ payload")
    return json.loads(m.group(1))


def navigation(page_html: str) -> list[NavLeaf]:
    """Every navigation leaf, in tree order, deduplicated by url."""
    flat = _flat(page_html)

    def res(i):
        return flat[i] if isinstance(i, int) and 0 <= i < len(flat) else i

    def node(i):
        n = res(i)
        return {k: res(v) for k, v in n.items()} if isinstance(n, dict) else None

    schools = [n for n in (node(i) for i in range(len(flat)))
               if n and n.get("type") == "group"
               and str(n.get("url", "")).startswith("/schools/")
               and str(n.get("url", "")).count("/") == 2]
    leaves: list[NavLeaf] = []
    seen_urls: set[str] = set()

    def walk(group: dict, school: str, trail: tuple[str, ...]):
        for child_i in group.get("children") or []:
            child = node(child_i)
            if not child:
                continue
            if child.get("type") == "group":
                walk(child, school, (*trail, child.get("label") or ""))
            elif child.get("type") == "link" and child.get("url") not in seen_urls:
                seen_urls.add(child.get("url"))
                leaves.append(NavLeaf(school, trail, child.get("label") or "",
                                      child.get("url") or "", child.get("pageId"),
                                      child.get("linkType")))

    seen_schools: set[str] = set()
    for group in schools:
        if group["url"] in seen_schools:
            continue
        seen_schools.add(group["url"])
        walk(group, group["url"].split("/")[2], ())
    return leaves


def prose_blocks(page_html: str) -> list[str]:
    return [s for s in _flat(page_html)
            if isinstance(s, str) and len(s) > 200 and "<" in s
            and not any(marker in s for marker in _NOT_PROSE)]


def page_prose(page_html: str) -> str:
    """The page's content as normalized text - what a reviewer reads."""
    text = " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", b)) for b in prose_blocks(page_html))
    return re.sub(r"\s+", " ", text).strip()


def prose_sha256(page_html: str) -> str:
    return hashlib.sha256(page_prose(page_html).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# credential candidates
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_key: str
    heading: str
    credential_type: str
    parent_candidate_key: str | None = None
    curriculum_code: str | None = None


@dataclass(slots=True)
class PageParse:
    candidates: list[Candidate] = field(default_factory=list)
    #: Headings that state admission/entry rules - kept for a future
    #: eligibility layer, never treated as credentials.
    admission_headings: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


_CURRICULUM = re.compile(r"Curriculum Code (\d{3})", re.I)


def candidates(page_html: str) -> PageParse:
    """Credential candidates from the page's own headings. Deterministic.

    <h3> sections:  "Major Requirements" -> major; "...Minor..." -> minor;
                    "Certificate ..." -> certificate; "Entry Requirements" /
                    "Admission" -> admission (not a credential); other
                    "...Program(s)" -> unknown (visible, never guessed).
    <p><strong> sub-headings inside the major section:
                    "Option X, ..." -> option of that major;
                    "... Interdisciplinary Major ..." -> interdisciplinary_major.
    """
    out = PageParse()
    seen: set[str] = set()

    def add(c: Candidate):
        if c.candidate_key not in seen:
            seen.add(c.candidate_key)
            out.candidates.append(c)

    section = None
    for block in prose_blocks(page_html):
        for m in re.finditer(r"<h3[^>]*>(.*?)</h3>|<p[^>]*>\s*<strong>(.*?)</strong>", block, re.S):
            is_h3 = m.group(1) is not None
            text = re.sub(r"\s+", " ", html_lib.unescape(
                re.sub(r"<[^>]+>", "", m.group(1) if is_h3 else m.group(2)))).strip()
            if not text:
                continue
            code = (_CURRICULUM.search(text) or [None, None])[1]
            if is_h3:
                low = text.lower()
                section = low
                if re.search(r"\bentry requirements\b|\badmission\b", low):
                    out.admission_headings.append(text)
                elif re.search(r"\bmajor requirements\b|\brequirements for the major\b", low):
                    add(Candidate("major", text, "major"))
                elif "minor" in low:
                    add(Candidate(_slug(text), text, "minor"))
                elif "certificate" in low:
                    # "Certificate Programs", "Post-Bacc Certificate in ..." (v2)
                    add(Candidate(_slug(text), text, "certificate", curriculum_code=code))
                elif re.search(r"\bbachelor of\b", low):
                    # A separate degree curriculum headed on its own, e.g.
                    # "Bachelor of Arts in Speech and Hearing Sciences in
                    # Linguistics" (v2).
                    add(Candidate(_slug(text), text, "major", curriculum_code=code))
                elif "program" in low and "courses" not in low:
                    add(Candidate(_slug(text), text, "unknown"))
            elif section and "major requirements" in section:
                if opt := re.match(r"Option ([A-Z])\b", text):
                    add(Candidate(f"major/option-{opt.group(1).lower()}", text, "option",
                                  parent_candidate_key="major", curriculum_code=code))
                elif "interdisciplinary major" in text.lower():
                    add(Candidate(_slug(text), text, "interdisciplinary_major",
                                  curriculum_code=code))
    return out


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RegistryStats:
    pages_seen: int = 0
    pages_inserted: int = 0
    pages_updated: int = 0
    candidates_inserted: int = 0
    candidates_unchanged: int = 0
    candidates_retired: list[str] = field(default_factory=list)


class CatalogRegistry:
    def __init__(self, session: Session, catalog_key: str = "nb-undergrad") -> None:
        self.session = session
        self.catalog_key = catalog_key

    def discover(self, page_html: str, catalog_year: str,
                 now: datetime | None = None) -> RegistryStats:
        """Upsert every navigation page. Idempotent; never deletes - a page
        missing from a later crawl keeps its row (and last_seen_at says when
        it was last seen)."""
        now = now or datetime.now(UTC)
        stats = RegistryStats()
        existing = {p.url_path: p for p in self.session.scalars(
            select(CatalogPage).where(CatalogPage.catalog_key == self.catalog_key,
                                      CatalogPage.catalog_year == catalog_year))}
        for leaf in navigation(page_html):
            stats.pages_seen += 1
            fields = {"source_page_id": leaf.source_page_id, "title": leaf.title,
                      "school_slug": leaf.school_slug, "trail": " / ".join(leaf.trail),
                      "page_class": leaf.page_class, "subject_code": leaf.subject_code}
            page = existing.get(leaf.url_path)
            if page is None:
                self.session.add(CatalogPage(
                    catalog_key=self.catalog_key, catalog_year=catalog_year,
                    url_path=leaf.url_path, first_seen_at=now, last_seen_at=now, **fields))
                stats.pages_inserted += 1
            else:
                if any(getattr(page, k) != v for k, v in fields.items()):
                    for k, v in fields.items():
                        setattr(page, k, v)
                    stats.pages_updated += 1
                page.last_seen_at = now
        self.session.flush()
        return stats

    def record_fetch(self, page: CatalogPage, page_html: str, snapshot_id,
                     now: datetime | None = None) -> RegistryStats:
        """Record an archived snapshot and (re-)extract candidates.

        Candidates are keyed per page; re-extraction from the same content is a
        no-op. A candidate whose heading disappears is reported as retired but
        its row is kept - a curated version may still reference it.
        """
        now = now or datetime.now(UTC)
        stats = RegistryStats()
        page.snapshot_id = snapshot_id
        page.prose_sha256 = prose_sha256(page_html)
        page.fetched_at = now
        parsed = candidates(page_html)
        existing = {c.candidate_key: c for c in self.session.scalars(
            select(ProgramCandidate).where(ProgramCandidate.catalog_page_id == page.id))}
        for c in parsed.candidates:
            row = existing.get(c.candidate_key)
            if row is None:
                self.session.add(ProgramCandidate(
                    catalog_page_id=page.id, candidate_key=c.candidate_key, heading=c.heading,
                    credential_type=c.credential_type, parent_candidate_key=c.parent_candidate_key,
                    curriculum_code=c.curriculum_code, extraction_method=EXTRACTOR_VERSION))
                stats.candidates_inserted += 1
            else:
                row.heading, row.credential_type = c.heading, c.credential_type
                row.parent_candidate_key, row.curriculum_code = c.parent_candidate_key, c.curriculum_code
                row.extraction_method = EXTRACTOR_VERSION
                stats.candidates_unchanged += 1
        stats.candidates_retired = sorted(set(existing) - {c.candidate_key for c in parsed.candidates})
        self.session.flush()
        return stats


__all__ = ["EXTRACTOR_VERSION", "Candidate", "CatalogRegistry", "NavLeaf", "PageParse",
           "RegistryStats", "candidates", "navigation", "page_prose", "prose_sha256"]
