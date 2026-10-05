"""Planning Engine domain (Phase 6.5): a semester-level COURSE plan and its evidence.

```
Degree Engine          "what remains?"            (app.services.audit)       - reused
course eligibility     "may the student take it?" (app.services.course_eligibility) - reused
Planning Engine        "which courses, which term?" (app.services.planning)  - this phase
Schedule Engine        "which section, which time?"                          - NOT this phase
```

Every planned course carries structured reasons and evidence, so a later
explanation layer can READ why a course was placed instead of inventing it.
No field here is free text that a rule depends on; `message` fields are for
people, `code` fields are for programs.

Vocabulary is deliberate:

  * the plan is "deterministic", never "optimal" - the selection is a
    documented greedy procedure, not a solved optimization problem;
  * historical offerings are "evidence", never a schedule;
  * a prerequisite met only by courses the plan itself schedules is
    CONDITIONAL - the planner never assumes a future grade.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.scenario import ScenarioAssumption, ScenarioTarget

#: Bumped whenever the same inputs could produce a different plan.
PLANNING_ENGINE_VERSION = "6.5.0"


class EligibilityState(StrEnum):
    """Whether the student may take a course in the planned term."""

    SATISFIED_BY_HISTORY = "satisfied_by_history"      # met by recorded coursework
    CONDITIONAL_ON_PLAN = "conditional_on_plan"        # met only if planned/in-progress pass
    UNSATISFIED = "unsatisfied"                        # not met even then
    NEEDS_CONFIRMATION = "needs_confirmation"          # Phase 6.4 says UNKNOWN - never planned


class OfferingEvidenceKind(StrEnum):
    CONFIRMED_IN_TERM = "confirmed_in_term"                  # SOC data for that term lists it
    NOT_OFFERED_IN_TERM = "not_offered_in_term"   # that term's SOC data is complete and omits it
    HISTORICAL_SAME_SEASON = "historically_offered_in_season"  # earlier terms of that season
    OTHER_SEASONS_ONLY = "offered_in_other_seasons_only"
    NO_EVIDENCE = "no_offering_evidence"


class Severity(StrEnum):
    BLOCKER = "blocker"                         # the planner cannot safely proceed for this
    NEEDS_CONFIRMATION = "needs_confirmation"   # a student/advisor must confirm something
    WARNING = "warning"                         # usable, with stated uncertainty


class Reason(StrEnum):
    SATISFIES_REQUIREMENT = "satisfies_requirement"
    PREREQUISITES_SATISFIED_BY_HISTORY = "prerequisites_satisfied_by_history"
    PREREQUISITES_CONDITIONAL_ON_PLAN = "prerequisites_conditional_on_plan"
    NO_PREREQUISITE_PUBLISHED = "no_prerequisite_published"
    REQUIRED_PREREQUISITE_FOR = "required_prerequisite_for"
    REQUIRED_COREQUISITE_FOR = "required_corequisite_for"
    UNLOCKS_DOWNSTREAM_COURSE = "unlocks_downstream_course"
    CONFIRMED_OFFERING_IN_TERM = "confirmed_offering_in_term"
    HISTORICALLY_OFFERED_IN_SEASON = "historically_offered_in_season"
    RULES_FROM_EARLIER_TERM = "rules_from_earlier_term"
    SELECTED_BY_CANONICAL_TIE_BREAK = "selected_by_canonical_tie_break"


class PlanStatus(StrEnum):
    COVERS_ALL_REQUIREMENTS = "covers_all_requirements"   # projected: all met or provisionally
    PARTIAL = "partial"                                   # some requirements could not be planned
    NOTHING_TO_PLAN = "nothing_to_plan"                   # no requirement remains


class PlanningConstraints(BaseModel):
    """Course-load PREFERENCES - CoursePilot planning defaults, NOT Rutgers rules.

    Rutgers publishes credit-load rules per school; CoursePilot does not model
    them, so these numbers are planner settings a student may change and are
    reported as such (`PlanMetadata.constraints_source`, set by the server -
    a client cannot relabel its own numbers as policy).
    """

    model_config = ConfigDict(extra="forbid")

    max_credits_per_term: Decimal = Field(default=Decimal("15"), ge=1, le=24)
    max_courses_per_term: int = Field(default=5, ge=1, le=8)
    max_terms: int = Field(default=8, ge=1, le=12)
    include_summer: bool = False
    include_winter: bool = False


CONSTRAINTS_SOURCE = "coursepilot_planning_setting_not_rutgers_policy"


class OfferingEvidence(BaseModel):
    kind: OfferingEvidenceKind
    target_term: str
    observed_terms: list[str] = Field(default_factory=list)
    same_season_terms: list[str] = Field(default_factory=list)
    most_recent_term: str | None = None
    #: The term whose PUBLISHED prerequisites/co-requisites were applied.
    rules_term: str | None = None
    #: Whether SOC data for the target term itself is loaded, and how fully.
    target_term_coverage: str | None = None


class ConditionalDependency(BaseModel):
    """A prerequisite course the plan (or an in-progress term) must PASS first."""

    course: str
    term_code: str
    source: str                       # "planned" | "in_progress"
    required_grade: str | None = None  # from the interpreted minimum-grade condition


class PrerequisiteEvidence(BaseModel):
    state: EligibilityState
    rules_term: str | None = None
    raw_text: str | None = None
    canonical_text: str | None = None
    combination: str | None = None             # how prerequisite and co-requisite combine
    corequisite_text: str | None = None
    conditional_on: list[ConditionalDependency] = Field(default_factory=list)
    unknown_reasons: list[str] = Field(default_factory=list)


class RequirementContribution(BaseModel):
    requirement_code: str
    requirement_name: str
    requirement_system: str
    #: Always True for a planned course: it counts only once passed.
    provisional: bool = True


class PlannedCourse(BaseModel):
    course: str
    title: str
    credits: Decimal
    term_code: str
    reasons: list[Reason]
    requirements: list[RequirementContribution] = Field(default_factory=list)
    prerequisite: PrerequisiteEvidence
    offering: OfferingEvidence
    corequisite_group: list[str] = Field(default_factory=list)
    #: Courses this one is a prerequisite/co-requisite for, in this plan or its targets.
    unlocks: list[str] = Field(default_factory=list)
    #: How many other equally ranked candidates existed (a canonical tie-break chose).
    interchangeable_alternatives: int = 0


class PlanTerm(BaseModel):
    term_code: str
    label: str
    courses: list[PlannedCourse] = Field(default_factory=list)
    credits: Decimal = Decimal(0)


class PlanningIssue(BaseModel):
    severity: Severity
    code: str
    message: str
    requirement_code: str | None = None
    course: str | None = None
    details: dict = Field(default_factory=dict)


class RequirementProjection(BaseModel):
    """A leaf requirement before and after the plan, per the Degree Engine."""

    requirement_code: str
    requirement_name: str
    status_before: str
    status_after: str


class PlanMetadata(BaseModel):
    planning_engine_version: str = PLANNING_ENGINE_VERSION
    audit_engine_version: str
    start_term: str
    constraints: PlanningConstraints
    constraints_source: str = CONSTRAINTS_SOURCE
    #: Fingerprints that identify the inputs, so a later phase can tell a plan
    #: is stale: the student's record, the program's rules, the SOC data.
    academic_fingerprint: str
    rules_fingerprint: str
    offering_dataset: list[str]


class PlanResult(BaseModel):
    target: ScenarioTarget
    status: PlanStatus
    terms: list[PlanTerm] = Field(default_factory=list)
    issues: list[PlanningIssue] = Field(default_factory=list)
    requirements: list[RequirementProjection] = Field(default_factory=list)
    assumptions: list[ScenarioAssumption] = Field(default_factory=list)
    metadata: PlanMetadata

    def canonical_json(self) -> str:
        """Byte-stable serialization (no timestamps exist in the model)."""
        return self.model_dump_json()


__all__ = ["CONSTRAINTS_SOURCE", "PLANNING_ENGINE_VERSION",
           "ConditionalDependency", "EligibilityState",
           "OfferingEvidence", "OfferingEvidenceKind", "PlanMetadata", "PlanResult", "PlanStatus",
           "PlanTerm", "PlannedCourse", "PlanningConstraints", "PlanningIssue",
           "PrerequisiteEvidence", "Reason", "RequirementContribution", "RequirementProjection",
           "Severity"]
