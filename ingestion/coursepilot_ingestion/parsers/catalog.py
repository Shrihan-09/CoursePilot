"""Parser for Rutgers catalog program pages.

Promoted from `scripts/probe_cs_program.py`. Structural extraction only - no
business meaning, no repair. A course entry that does not fit the expected
shape is reported as a failure, never coerced.

## How the data is reached

The page is a Nuxt 3 SPA. Nuxt embeds its SSR payload in a
`<script id="__NUXT_DATA__">` tag as a FLAT array in which every nested value
is an integer index into that same array. The course catalog itself is one
large HTML block inside that payload.

So there are two layers: unwrap the Nuxt payload, then parse the HTML block.

## The HTML block shape (observed, both catalog years)

    <strong>01:198:111</strong>
    <strong><em>Introduction to Computer Science (4)<br></em></strong>
    Description text runs until the next course code...

## Why this splits rather than matching whole entries

The obvious approach - one regex per entry - silently DROPPED courses. Some
titles contain an internal `</em></strong><strong><em>` split (01:198:110
"Principles to Computer Science" is a real example), which let the title group
run past the end of its own entry and swallow the NEXT course whole. That is
how 01:198:111 - a REQUIRED course for the major - went missing with no error.

So the block is split on the course-code marker first, which is unambiguous,
and each chunk is parsed independently. A malformed chunk can then only lose
itself, never its neighbour.
"""

from __future__ import annotations

import html as htmllib
import json
import logging
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from coursepilot_ingestion.catalog_schemas import RawCatalogCourse

logger = logging.getLogger(__name__)

NUXT_RE = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

# The entry boundary: a course code in its own <strong> tag. Used to SPLIT the
# block, so one malformed entry cannot consume the next.
COURSE_MARKER_RE = re.compile(r"<strong>\s*(\d{2}:\d{3}:\d{3})\s*</strong>")

# The leading emphasised run holds the title and, usually, its parenthesised
# credits. Captured as ONE unit so credits can be split off afterwards -
# 01:198:110 publishes a title with NO credits at all, so requiring the
# parentheses here would lose its title and description too.
TITLE_RUN_RE = re.compile(r"<em>(.*?)</em>\s*</strong>", re.DOTALL)

# Trailing "(4)" or "(3-4)" at the end of a title run.
TRAILING_CREDITS_RE = re.compile(r"\(([^()]{1,20})\)\s*$")

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def plain_text(value: str) -> str:
    """Strip tags, unescape entities, collapse whitespace."""
    return htmllib.unescape(WS_RE.sub(" ", TAG_RE.sub(" ", value))).strip()


@dataclass(slots=True)
class CatalogParseFailure:
    course_string: str | None
    error: str


@dataclass(slots=True)
class CatalogParseResult:
    courses: list[RawCatalogCourse] = field(default_factory=list)
    failures: list[CatalogParseFailure] = field(default_factory=list)
    # Prose blocks retained so requirement curation can be re-checked against
    # the exact published text.
    prose_blocks: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.courses) + len(self.failures)


class CatalogParser:
    """Parses a catalog program page into course entries."""

    MIN_BLOCK_CHARS = 300

    def _payload_strings(self, page_html: str) -> list[str]:
        match = NUXT_RE.search(page_html)
        if not match:
            raise ValueError(
                "no __NUXT_DATA__ payload found; the catalog page shape has changed"
            )
        try:
            flat = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise ValueError(f"__NUXT_DATA__ is not valid JSON: {exc}") from exc
        if not isinstance(flat, list):
            raise ValueError(
                f"expected the Nuxt payload to be an array, got {type(flat).__name__}"
            )
        return [x for x in flat if isinstance(x, str)]

    @staticmethod
    def _split_entries(block: str) -> list[tuple[str, str]]:
        """Split the course block into (course_code, chunk) pairs.

        Each chunk runs from just after its code marker to just before the
        next one, so entries cannot bleed into each other.
        """
        markers = list(COURSE_MARKER_RE.finditer(block))
        entries: list[tuple[str, str]] = []
        for i, m in enumerate(markers):
            end = markers[i + 1].start() if i + 1 < len(markers) else len(block)
            entries.append((m.group(1).strip(), block[m.end() : end]))
        return entries

    @staticmethod
    def _parse_chunk(chunk: str) -> tuple[str, str, str]:
        """Pull (title, credits, description) out of one entry chunk.

        Title and credits are parsed independently, because the catalog does
        not always publish credits: 01:198:110 has a title and a description
        but no "(N)". Requiring the parentheses would discard all three.
        """
        run = TITLE_RUN_RE.search(chunk)
        if run is None:
            # No emphasised title run at all - keep the chunk as description
            # rather than dropping a real course.
            return "", "", plain_text(chunk)

        title_text = plain_text(run.group(1))
        credits_raw = ""

        trailing = TRAILING_CREDITS_RE.search(title_text)
        if trailing:
            credits_raw = trailing.group(1).strip()
            title_text = title_text[: trailing.start()].strip()

        description = plain_text(chunk[run.end() :])
        return title_text, credits_raw, description

    def parse(self, page_html: str) -> CatalogParseResult:
        strings = self._payload_strings(page_html)
        blocks = sorted(
            (s for s in strings if len(s) > self.MIN_BLOCK_CHARS), key=len, reverse=True
        )

        result = CatalogParseResult(prose_blocks=[plain_text(b) for b in blocks])

        # The course block is whichever block contains course entries. Chosen
        # by content rather than position, so an added block does not silently
        # shift which one we parse.
        course_block = next((b for b in blocks if COURSE_MARKER_RE.search(b)), None)
        if course_block is None:
            logger.warning("no course entries found in any catalog text block")
            return result

        for code, chunk in self._split_entries(course_block):
            title, credits_raw, description = self._parse_chunk(chunk)
            try:
                result.courses.append(
                    RawCatalogCourse(
                        course_string=code,
                        title=title,
                        credits_raw=credits_raw,
                        description=description,
                    )
                )
            except ValidationError as exc:
                result.failures.append(
                    CatalogParseFailure(
                        course_string=code,
                        error="; ".join(
                            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                            for e in exc.errors()[:3]
                        ),
                    )
                )

        if result.failures:
            logger.warning(
                "parsed %d/%d catalog courses (%d failed)",
                len(result.courses),
                result.total,
                len(result.failures),
            )
        return result
