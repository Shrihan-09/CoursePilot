"""Course/professor intelligence vocabulary (Phase 7.2.4). RANKING evidence only.

Nothing here is academic truth: no signal can make an invalid course or
section eligible, and no difficulty or workload is ever inferred. Where
evidence is missing the answer is NOT_ENOUGH_DATA - never "easy".
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Confidence(StrEnum):
    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class Provenance(BaseModel):
    source: str                                  # e.g. "coursepilot_feedback"
    sample_size: int = Field(ge=0)
    window_start: str | None = None              # earliest term in the sample
    window_end: str | None = None                # latest term in the sample
    confidence: Confidence
    updated_at: datetime | None = None


class Dimension(StrEnum):
    WORKLOAD = "workload"        # 1 very light .. 5 very heavy
    DIFFICULTY = "difficulty"    # 1 very easy .. 5 very difficult


class AggregateState(StrEnum):
    NOT_ENOUGH_DATA = "not_enough_data"
    AVAILABLE = "available"


class FeedbackAggregate(BaseModel):
    """Only ever an aggregate. Below the minimum sample nothing about the
    responses is returned - not a distribution, not a median."""

    course: str
    instructor_key: str | None = None
    dimension: Dimension
    state: AggregateState
    distribution: dict[int, int] | None = None   # rating -> count, AVAILABLE only
    median: float | None = None
    provenance: Provenance
    wording: str


class IdentityMatch(StrEnum):
    VERIFIED = "verified"          # a human-verified link exists
    UNVERIFIED = "unverified"      # a plausible candidate, not verified: never attached
    AMBIGUOUS = "ambiguous"        # several candidates or an initial-only name
    NONE = "none"


class SourceAccess(StrEnum):
    PERMITTED = "permitted"
    PENDING_APPROVAL = "pending_approval"
    NOT_PERMITTED = "not_permitted"


class ExternalSource(BaseModel):
    source: str
    access: SourceAccess
    basis: str                     # why - the terms/permission finding, briefly


class ProfessorSignal(BaseModel):
    """A summarised, provenance-carrying signal - attachable ONLY on VERIFIED identity."""

    instructor_key: str
    source: str
    identity: IdentityMatch
    themes: list[str] = Field(default_factory=list)   # e.g. "organized", "weekly_quizzes"
    provenance: Provenance


__all__ = ["AggregateState", "Confidence", "Dimension", "ExternalSource", "FeedbackAggregate",
           "IdentityMatch", "ProfessorSignal", "Provenance", "SourceAccess"]
