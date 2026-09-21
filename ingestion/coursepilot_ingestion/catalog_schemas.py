"""Pydantic schemas for catalog ingestion.

Same two-model pattern as the SOC pipeline: a permissive model for what the
source sends, a strict model for CoursePilot's own vocabulary.

The catalog differs from SOC in one way that matters here: **credits can be a
range** ("3-4"). SOC's single numeric `credits` cannot express that, which is
why catalog credits are normalized to `credits_min`/`credits_max` and stored
on `catalog_course_entry` rather than written back onto `course`.
"""

from __future__ import annotations

import re
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

COURSE_STRING_RE = re.compile(r"^\d{2}:\d{3}:\d{3}$")

# Observed catalog credit strings: "1", "3", "3-4", "4". The range form is the
# reason this parsing exists at all.
CREDITS_SINGLE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")
CREDITS_RANGE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)\s*$")


class RawCatalogCourse(BaseModel):
    """A course entry exactly as parsed out of the catalog page.

    Permissive: the catalog is prose-derived HTML, so anything unexpected is
    kept rather than discarded, and validation happens at the next stage.
    """

    model_config = ConfigDict(extra="allow")

    course_string: str
    title: str
    credits_raw: str
    description: str


class NormalizedCatalogCourse(BaseModel):
    """CoursePilot's canonical catalog entry.

    Strict. If one of these reaches the database it has already passed every
    check below.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    course_string: str
    catalog_year: str = Field(min_length=4)

    title: str | None = None
    description: str | None = None

    credits_min: Decimal | None = None
    credits_max: Decimal | None = None
    # The original string is retained so a parsing change can be re-checked
    # against what the catalog actually published.
    credits_raw: str | None = None

    source_url: str | None = None

    @field_validator("course_string")
    @classmethod
    def _check_course_string(cls, v: str) -> str:
        if not COURSE_STRING_RE.match(v):
            raise ValueError(
                f"course_string {v!r} does not match the Rutgers format "
                "'unit:subject:course' (e.g. '01:198:111')"
            )
        return v

    @field_validator("credits_min", "credits_max")
    @classmethod
    def _non_negative(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError(f"credits cannot be negative, got {v}")
        if v is not None and v > 24:
            raise ValueError(f"credits {v} is implausibly high; likely a parse error")
        return v

    @model_validator(mode="after")
    def _range_ordered(self) -> NormalizedCatalogCourse:
        if (
            self.credits_min is not None
            and self.credits_max is not None
            and self.credits_max < self.credits_min
        ):
            raise ValueError(
                f"credit range is inverted: {self.credits_min}-{self.credits_max}"
            )
        return self

    @property
    def is_credit_range(self) -> bool:
        return (
            self.credits_min is not None
            and self.credits_max is not None
            and self.credits_max != self.credits_min
        )


def parse_credits(raw: str | None) -> tuple[Decimal | None, Decimal | None]:
    """Parse a catalog credit string into (min, max).

    A single value yields min == max, so downstream code never has to special-
    case the two forms. Unparseable input yields (None, None) rather than a
    guess - an invented credit value would silently corrupt a degree audit.
    """
    if not raw:
        return (None, None)
    text = raw.strip()

    m = CREDITS_SINGLE_RE.match(text)
    if m:
        value = Decimal(m.group(1))
        return (value, value)

    m = CREDITS_RANGE_RE.match(text)
    if m:
        return (Decimal(m.group(1)), Decimal(m.group(2)))

    return (None, None)


class CatalogIngestionStats(BaseModel):
    """Outcome of one catalog pipeline run."""

    catalog_year: str | None = None
    parsed: int = 0
    parse_failed: int = 0
    validated: int = 0
    validation_failed: int = 0
    entries_inserted: int = 0
    entries_updated: int = 0

    descriptions_populated: int = 0
    credit_ranges: int = 0

    # Catalog courses with no matching `course` row. Reported, never created.
    unmapped: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    source_content_hash: str | None = None

    def summary(self) -> str:
        return (
            f"year={self.catalog_year} parsed={self.parsed} "
            f"validated={self.validated} failed={self.validation_failed} "
            f"entries(+{self.entries_inserted}/~{self.entries_updated}) "
            f"descriptions={self.descriptions_populated} "
            f"ranges={self.credit_ranges} unmapped={len(self.unmapped)}"
        )
