"""Normalizer: RawCatalogCourse -> NormalizedCatalogCourse.

The boundary between the catalog's vocabulary and CoursePilot's.

One decision worth stating: an empty description normalizes to `None`, not to
an empty string, so "the catalog has no description for this course" is
distinguishable from "the description is blank". Nothing here invents a value
the catalog did not publish.
"""

from __future__ import annotations

import logging

from coursepilot_ingestion.catalog_schemas import (
    NormalizedCatalogCourse,
    RawCatalogCourse,
    parse_credits,
)

logger = logging.getLogger(__name__)

# Descriptions shorter than this are almost always a parsing artifact (a
# stray fragment between entries) rather than a real description. Measured
# real descriptions run to hundreds of characters.
MIN_DESCRIPTION_CHARS = 30


class CatalogNormalizer:
    def __init__(self, catalog_year: str, source_url: str | None = None) -> None:
        self.catalog_year = catalog_year
        self.source_url = source_url

    def normalize(self, raw: RawCatalogCourse) -> NormalizedCatalogCourse:
        credits_min, credits_max = parse_credits(raw.credits_raw)

        description = (raw.description or "").strip()
        if len(description) < MIN_DESCRIPTION_CHARS:
            description = ""

        title = (raw.title or "").strip()

        return NormalizedCatalogCourse(
            course_string=raw.course_string.strip(),
            catalog_year=self.catalog_year,
            title=title or None,
            description=description or None,
            credits_min=credits_min,
            credits_max=credits_max,
            credits_raw=(raw.credits_raw or "").strip() or None,
            source_url=self.source_url,
        )
