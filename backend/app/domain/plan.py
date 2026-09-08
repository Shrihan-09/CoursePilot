"""Structured planner output.

Shapes only — no planning logic exists yet.

Design note (deviation from the conceptual schema in the project brief):
the brief's example nested a `validation` object with fixed boolean fields
(`prerequisites_valid`, `credit_load_valid`, ...). We instead attach a
`ValidationReport` carrying a list of findings. Same principle, but:

  * adding a new check does not change the response schema;
  * a check can report "could not determine" rather than being forced into
    true/false — which is exactly the case where a boolean would lie;
  * every finding carries its own provenance and remediation.

The frontend renders findings generically, so new validators ship without a
frontend change.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.provenance import SourceRef
from app.domain.validation import ValidationReport


class PlanStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    # Produced when the planner ran out of replan attempts. The partial plan
    # is still returned so the student can see how far we got.
    NEEDS_REVISION = "needs_revision"
    # We lack the authoritative data to answer. Better than a confident guess.
    INSUFFICIENT_DATA = "insufficient_data"


class PlannedCourse(BaseModel):
    """A course the planner proposes.

    `course_id` is a CoursePilot database id, not a model-generated string.
    The planner selects from a retrieved candidate set; it never invents an
    identifier. Resolution happens before validation, and an unresolvable id
    is a hard failure, not a warning.
    """

    course_id: str
    course_code: str = Field(description="Display code, resolved from the database.")
    title: str
    credits: float | None = None
    section_id: str | None = Field(
        default=None, description="Set only once a concrete section has been chosen."
    )
    satisfies_requirement_ids: list[str] = Field(
        default_factory=list,
        description="Requirements this course would satisfy, per authoritative rules.",
    )
    rationale: str | None = Field(
        default=None,
        description="Model-authored explanation. Advisory text only — never load-bearing.",
    )
    sources: list[SourceRef] = Field(default_factory=list)


class SemesterPlan(BaseModel):
    term_code: str
    term_label: str = Field(description='Display form, e.g. "Spring 2027".')
    courses: list[PlannedCourse] = Field(default_factory=list)
    total_credits: float = 0.0


class PlanResponse(BaseModel):
    """What the API returns for a planning request.

    `status` and `validation` are set by the deterministic core. If the two
    ever disagree, the ValidationReport wins.
    """

    status: PlanStatus
    semesters: list[SemesterPlan] = Field(default_factory=list)
    validation: ValidationReport
    requirements_satisfied: list[str] = Field(default_factory=list)
    requirements_remaining: list[str] = Field(default_factory=list)

    summary: str | None = Field(
        default=None, description="Model-authored prose summary. Advisory only."
    )
    # Surfaced in the UI as a standing caveat. CoursePilot is a planning aid;
    # the student's official Rutgers advising record governs.
    disclaimers: list[str] = Field(default_factory=list)

    replan_attempts: int = Field(
        default=0, description="How many validate/replan cycles were needed."
    )
    trace_id: str | None = Field(
        default=None, description="Correlates this response with its server-side trace."
    )
