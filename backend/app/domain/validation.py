"""The validation verdict contract.

This is the most important type in CoursePilot. The deterministic core emits
these; the LLM may never fabricate one. A plan is valid if and only if a
`ValidationReport` produced by application code says so.

Nothing here is implemented yet — these are the shapes the validators will
fill in. See docs/AGENT_ARCHITECTURE.md ("Deterministic validation").
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.provenance import SourceRef


class Severity(StrEnum):
    BLOCKING = "blocking"   # plan is invalid; must replan
    WARNING = "warning"     # plan is allowed but the student should be told
    INFO = "info"


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    # Distinct from FAILED on purpose. "We could not verify this" is not the
    # same as "this is wrong", and collapsing the two either blocks valid
    # plans or silently green-lights unverified ones. The UI must render
    # INDETERMINATE differently from PASSED.
    INDETERMINATE = "indeterminate"
    NOT_APPLICABLE = "not_applicable"


class CheckKind(StrEnum):
    COURSE_EXISTS = "course_exists"
    PREREQUISITE = "prerequisite"
    COREQUISITE = "corequisite"
    OFFERING_AVAILABLE = "offering_available"
    REQUIREMENT_SATISFACTION = "requirement_satisfaction"
    CREDIT_LOAD = "credit_load"
    SCHEDULE_CONFLICT = "schedule_conflict"
    DUPLICATE_CREDIT = "duplicate_credit"
    PROGRAM_APPLICABILITY = "program_applicability"


class Finding(BaseModel):
    """One thing a validator noticed."""

    kind: CheckKind
    status: CheckStatus
    severity: Severity
    message: str = Field(description="Human-readable, shown to the student.")
    subject_ref: str | None = Field(
        default=None, description="What this is about, e.g. a course or requirement id."
    )
    # Why the student can trust this. A BLOCKING finding about Rutgers policy
    # with no sources is a bug — we would be asserting a rule we cannot back.
    sources: list[SourceRef] = Field(default_factory=list)
    remediation: str | None = Field(
        default=None, description="What the student (or the replanner) could do about it."
    )


class ValidationReport(BaseModel):
    """The verdict on a proposed plan.

    `is_valid` is computed from the findings, never set by a caller and never
    by the model.
    """

    findings: list[Finding] = Field(default_factory=list)
    checks_run: list[CheckKind] = Field(
        default_factory=list, description="Which validators actually executed."
    )
    checks_skipped: list[CheckKind] = Field(
        default_factory=list,
        description="Validators that could not run, e.g. because data was missing.",
    )

    @property
    def is_valid(self) -> bool:
        return not any(f.severity is Severity.BLOCKING for f in self.findings)

    @property
    def has_unverifiable_claims(self) -> bool:
        """True when we could not fully check the plan. The UI must not
        present such a plan as confirmed."""
        return bool(self.checks_skipped) or any(
            f.status is CheckStatus.INDETERMINATE for f in self.findings
        )
