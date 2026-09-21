"""Pydantic schemas for the SOC ingestion pipeline.

Two distinct models, and the distinction matters:

  * `RawSocCourse`  — mirrors what the SOC API actually sends. Deliberately
    permissive, because we do not control that shape and a rigid model here
    would reject real data on the first upstream tweak.

  * `NormalizedCourse` — CoursePilot's own vocabulary. Strict, because we DO
    control this shape, and everything downstream depends on it.

Normalization is the boundary between "their format" and "our format". Keeping
the two models separate means an upstream field rename changes one mapping
function, not the whole codebase.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Observed in every one of 4,400 records: "unit:subject:course", e.g. "01:013:120".
COURSE_STRING_RE = re.compile(r"^\d{2}:\d{3}:\d{3}$")
# Course numbers are 3 digits in all observed data, but the regex is kept
# separate so a future 4-digit unit does not require touching course parsing.
COURSE_NUMBER_RE = re.compile(r"^\d{3}$")

VALID_LEVELS = {"U", "G"}


class RawSocCourse(BaseModel):
    """A course object exactly as SOC returns it.

    `extra="allow"` on purpose: the API is undocumented and unversioned. If
    Rutgers adds a field we want to keep the payload intact rather than have
    Pydantic silently discard it.
    """

    model_config = ConfigDict(extra="allow")

    courseString: str
    offeringUnitCode: str
    subject: str
    courseNumber: str
    # Observed as two spaces when absent, so the default matches reality.
    supplementCode: str = "  "

    title: str
    expandedTitle: str | None = None
    courseDescription: str | None = None

    # Union order matters: Decimal first so 3 stays exact rather than becoming
    # a float. Nullable because 11.1% of observed records have no credits.
    credits: Decimal | None = None
    creditsObject: dict | None = None

    level: str | None = None
    campusCode: str | None = None
    subjectDescription: str | None = None
    school: dict | None = None
    preReqNotes: str | None = None
    synopsisUrl: str | None = None
    openSections: int | None = None
    sections: list[dict] = Field(default_factory=list)
    coreCodes: list[dict] = Field(default_factory=list)


class NormalizedCourse(BaseModel):
    """CoursePilot's canonical course.

    Strict: unknown fields are rejected, and the invariants below are enforced
    at construction. If a course reaches the database, it has already passed
    every check in this class.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # --- natural key ---
    offering_unit_code: str = Field(min_length=1, max_length=8)
    subject_code: str = Field(min_length=1, max_length=8)
    course_number: str = Field(min_length=1, max_length=16)
    supplement_code: str = Field(default="", max_length=8)

    course_string: str

    # --- content ---
    title: str = Field(min_length=1)
    title_abbrev: str | None = None
    description: str | None = None

    credits: Decimal | None = None
    credits_description: str | None = None

    level: str | None = None
    school_code: str | None = None
    school_description: str | None = None
    subject_description: str | None = None
    prereq_notes_raw: str | None = None
    synopsis_url: str | None = None

    # --- offering context (term/campus scope of this observation) ---
    term_code: str = Field(min_length=1)
    campus_code: str = Field(min_length=1)
    open_sections_observed: int | None = None
    section_count_observed: int | None = None

    @field_validator("course_string")
    @classmethod
    def _check_course_string(cls, v: str) -> str:
        if not COURSE_STRING_RE.match(v):
            raise ValueError(
                f"courseString {v!r} does not match the observed Rutgers format "
                "'unit:subject:course' (e.g. '01:013:120')"
            )
        return v

    @field_validator("course_number")
    @classmethod
    def _check_course_number(cls, v: str) -> str:
        if not COURSE_NUMBER_RE.match(v):
            raise ValueError(f"course_number {v!r} is not a 3-digit Rutgers course number")
        return v

    @field_validator("credits")
    @classmethod
    def _check_credits(cls, v: Decimal | None) -> Decimal | None:
        # None is legitimate (11.1% of real records). Negative is not, and a
        # value above the observed maximum of 16 signals a parsing error
        # rather than an unusual course.
        if v is None:
            return v
        if v < 0:
            raise ValueError(f"credits cannot be negative, got {v}")
        if v > 24:
            raise ValueError(f"credits {v} is implausibly high; likely a parse error")
        return v

    @field_validator("level")
    @classmethod
    def _check_level(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_LEVELS:
            raise ValueError(f"level {v!r} not in observed values {sorted(VALID_LEVELS)}")
        return v

    @property
    def natural_key(self) -> tuple[str, str, str, str]:
        """The tuple that uniquely identifies this course.

        Excludes campus by design — see the Course model docstring.
        """
        return (
            self.offering_unit_code,
            self.subject_code,
            self.course_number,
            self.supplement_code,
        )


class IngestionStats(BaseModel):
    """Outcome of one pipeline run. Returned, logged, and asserted on in tests."""

    fetched: int = 0
    parsed: int = 0
    parse_failed: int = 0
    validated: int = 0
    validation_failed: int = 0
    courses_inserted: int = 0
    courses_updated: int = 0
    offerings_inserted: int = 0
    offerings_updated: int = 0
    subjects_inserted: int = 0
    errors: list[str] = Field(default_factory=list)
    source_content_hash: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def summary(self) -> str:
        return (
            f"fetched={self.fetched} parsed={self.parsed} "
            f"parse_failed={self.parse_failed} validated={self.validated} "
            f"validation_failed={self.validation_failed} "
            f"courses(+{self.courses_inserted}/~{self.courses_updated}) "
            f"offerings(+{self.offerings_inserted}/~{self.offerings_updated})"
        )
