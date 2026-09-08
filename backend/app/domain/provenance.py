"""Provenance primitives.

Architectural rule: every Rutgers-derived fact that reaches a student must be
traceable to the source it came from. These types are the vocabulary for that.

They are deliberately framework-free (no SQLAlchemy, no FastAPI) so the same
shapes can be used by the API layer, the ingestion pipeline, and tests.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class SourceKind(StrEnum):
    """How a fact entered the system.

    Ordering matters: `RUTGERS_OFFICIAL_*` outrank everything else when two
    sources disagree. Conflict-resolution policy lives in
    docs/DATA_MODEL.md ("Source precedence").
    """

    RUTGERS_OFFICIAL_API = "rutgers_official_api"
    RUTGERS_OFFICIAL_CATALOG = "rutgers_official_catalog"
    RUTGERS_DEPARTMENT_PAGE = "rutgers_department_page"
    RUTGERS_PDF = "rutgers_pdf"
    STUDENT_SELF_REPORTED = "student_self_reported"
    MANUAL_CURATION = "manual_curation"
    DERIVED = "derived"  # computed by CoursePilot from other sourced records


class VerificationStatus(StrEnum):
    """How much we trust a record.

    Anything not `VERIFIED` must be surfaced to the student as provisional.
    The planner is never permitted to present `UNVERIFIED` or `CONFLICTED`
    data as settled fact.
    """

    VERIFIED = "verified"          # confirmed against an authoritative source
    UNVERIFIED = "unverified"      # ingested but not yet checked
    STALE = "stale"                # source has changed since retrieval
    CONFLICTED = "conflicted"      # two sources disagree; needs resolution
    DEPRECATED = "deprecated"      # superseded; retained for history


class SourceRef(BaseModel):
    """A pointer to where a fact came from.

    Persisted as a `data_source` row plus a per-record FK — see
    docs/DATA_MODEL.md. This is the in-memory representation.
    """

    source_id: str = Field(description="Stable id of the data_source record.")
    kind: SourceKind
    url: HttpUrl | None = Field(
        default=None, description="Canonical URL of the source document, if web-derived."
    )
    retrieved_at: datetime = Field(description="When CoursePilot fetched this content.")
    source_last_modified: datetime | None = Field(
        default=None, description="Last-modified reported by the source, if available."
    )

    # Rutgers data is term-scoped: a prerequisite or requirement true for one
    # catalog year is not automatically true for another. Never drop these.
    academic_year: str | None = Field(
        default=None, description='Catalog year the fact applies to, e.g. "2026-2027".'
    )
    term_code: str | None = Field(
        default=None,
        description=(
            "Term this fact applies to. Format is Rutgers-defined — "
            "TODO(rutgers-source): confirm the official term code format."
        ),
    )

    data_version: int = Field(
        default=1, description="Monotonic version of this record within its source."
    )
    verification: VerificationStatus = VerificationStatus.UNVERIFIED
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Extraction confidence. Only meaningful for parsed/derived records.",
    )
    note: str | None = None


class Sourced[T](BaseModel):
    """Wraps a value with its provenance.

    Use this at the boundary where data leaves the deterministic core and
    heads for the LLM or the UI, so a claim can never be rendered without the
    receipt that backs it.
    """

    value: T
    sources: list[SourceRef] = Field(
        min_length=1, description="At least one source. An unsourced Rutgers fact is a bug."
    )
