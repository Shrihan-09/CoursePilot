"""Versioned display contract. Modeled obligation progress is not graduation eligibility."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.audit import Allocation, AuditFinding, RequirementResult, RuleResult

METRIC_POLICY_VERSION = "modeled-obligations/1"


class ExactRatio(BaseModel):
    numerator: int
    denominator: int


class Contribution(BaseModel):
    units: ExactRatio
    percent: Decimal | None
    exact_percent: ExactRatio | None


class ProgressMetric(BaseModel):
    scope: Literal["modeled_overall", "primary_program", "school_core"]
    label: str
    availability: Literal["available", "unavailable"]
    denominator_units: int | None
    denominator_definition: str
    completed: Contribution
    projected_current: Contribution
    projected_plan: Contribution | None = None
    in_progress_increment: Contribution | None = None
    planned_increment: Contribution | None = None
    remaining: Contribution
    unknown_obligations: int
    percentage_kind: Literal["confirmed_lower_bound", "exact", "unavailable"]
    status: str
    drill_down: str
    requirement_codes: list[str]
    requirement_results: list[RequirementResult]
    allocations: list[Allocation]
    explanation: str


class ProgressResponse(BaseModel):
    schema_version: str = "1"
    metric_policy_version: str = METRIC_POLICY_VERSION
    display_percent_decimal_places: int = 2
    generated_at: datetime
    audit_engine_version: str
    academic_fingerprint: str
    rules_fingerprint: str
    coverage: Literal["partial"] = "partial"
    graduation_eligibility: Literal["not_assessed"] = "not_assessed"
    academic_context: dict
    coverage_limitations: list[str]
    projection_state: Literal["available", "absent", "stale", "incomparable"]
    projection_assumptions: list[str] = Field(default_factory=list)
    metrics: list[ProgressMetric]
    gates: list[RuleResult]
    findings: list[AuditFinding]
