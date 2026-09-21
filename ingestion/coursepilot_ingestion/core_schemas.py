"""Pydantic schemas for SAS Core ingestion.

Core is unusual among CoursePilot's sources in that its two halves come from
two DIFFERENT authoritative places:

  * goal definitions + requirement structure -> SAS OUE page (prose, curated)
  * course -> goal eligibility               -> SOC `coreCodes` (structured)

So there are two raw models, not one. Keeping them separate is what lets the
authority matrix in docs/DATA_SOURCES.md be honest about which source backs
which fact.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

COURSE_STRING_RE = re.compile(r"^\d{2}:\d{3}:\d{3}$")
# Observed SAS Core codes are 2-5 alphanumeric characters (NS, HST, AHo, WCd,
# CCD). Deliberately permissive on case: the official page writes WCR/WCD, SOC
# writes WCr/WCd, and they are the same goal.
GOAL_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,5}$")


class RawCoreGoal(BaseModel):
    """A goal as curated from the official SAS page."""

    model_config = ConfigDict(extra="allow")

    code: str
    name: str
    description: str | None = None


class RawCoreCode(BaseModel):
    """One `coreCodes` entry exactly as SOC publishes it.

    Permissive, like every other raw model: SOC is undocumented and a new
    field must not cause a rejection.
    """

    model_config = ConfigDict(extra="allow")

    code: str
    description: str | None = None
    # MEASURED: `year`, `term` and `effective` echo the PAYLOAD's term, not a
    # goal's own validity window - every one of 1,541 Fall 2026 entries had
    # year=2026, term=9, effective=20269. They are therefore NOT used as a
    # catalog year. See docs/DATA_SOURCES.md.
    year: str | None = None
    term: str | None = None
    effective: str | None = None
    lastUpdated: int | None = None


class NormalizedCoreGoal(BaseModel):
    """A SAS Core learning goal in CoursePilot's vocabulary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=8)
    name: str = Field(min_length=1)
    description: str | None = None
    catalog_year: str = Field(min_length=4)

    @field_validator("code")
    @classmethod
    def _check_code(cls, v: str) -> str:
        if not GOAL_CODE_RE.match(v):
            raise ValueError(
                f"core goal code {v!r} does not look like a Rutgers core code "
                "(2-6 alphanumeric characters, e.g. 'NS', 'HST', 'AHo')"
            )
        return v

    @property
    def match_key(self) -> str:
        """Codes are matched case-insensitively: the official page writes
        WCR/WCD while SOC writes WCr/WCd for the same goals."""
        return self.code.casefold()


class NormalizedCoreEligibility(BaseModel):
    """One certified course -> goal mapping, from SOC.

    ELIGIBILITY, not satisfaction. This says the course *may* count toward the
    goal; whether it *does* is decided by the audit's allocator.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    course_string: str
    goal_code: str
    catalog_year: str = Field(min_length=4)
    # The SOC term this certification was observed in. Retained as provenance:
    # it is where we saw the fact, NOT a claim about the goal's validity.
    observed_term_code: str | None = None

    @field_validator("course_string")
    @classmethod
    def _check_course_string(cls, v: str) -> str:
        if not COURSE_STRING_RE.match(v):
            raise ValueError(
                f"course_string {v!r} does not match the Rutgers format "
                "'unit:subject:course' (e.g. '01:198:111')"
            )
        return v

    @field_validator("goal_code")
    @classmethod
    def _check_goal_code(cls, v: str) -> str:
        if not GOAL_CODE_RE.match(v):
            raise ValueError(f"goal code {v!r} is malformed")
        return v

    @property
    def match_key(self) -> str:
        return self.goal_code.casefold()


class CoreIngestionStats(BaseModel):
    """Outcome of one Core pipeline run."""

    catalog_year: str | None = None

    goals_defined: int = 0
    requirements_inserted: int = 0
    requirements_updated: int = 0

    core_codes_seen: int = 0
    eligibility_parsed: int = 0
    eligibility_inserted: int = 0
    eligibility_duplicates: int = 0

    courses_represented: int = 0

    # A certification whose course is not in the ingested SOC terms. Reported,
    # never used to manufacture a Course row.
    unresolved_courses: list[str] = Field(default_factory=list)
    # A SOC code with no matching curated goal (ITR, WC, SOEHS, GVT, ECN, CE).
    unmapped_goal_codes: dict[str, int] = Field(default_factory=dict)
    # Codes the curated definition claims but SOC never certifies.
    goals_without_eligibility: list[str] = Field(default_factory=list)

    errors: list[str] = Field(default_factory=list)
    source_content_hash: str | None = None

    def summary(self) -> str:
        return (
            f"year={self.catalog_year} goals={self.goals_defined} "
            f"requirements(+{self.requirements_inserted}/~{self.requirements_updated}) "
            f"core_codes={self.core_codes_seen} eligibility+{self.eligibility_inserted} "
            f"dupes={self.eligibility_duplicates} courses={self.courses_represented} "
            f"unresolved={len(self.unresolved_courses)} "
            f"unmapped_codes={len(self.unmapped_goal_codes)}"
        )
