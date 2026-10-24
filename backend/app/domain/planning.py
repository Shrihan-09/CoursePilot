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

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.course_ranking import CourseRankingEvidence
from app.domain.scenario import ScenarioAssumption, ScenarioTarget

#: Bumped whenever the same inputs could produce a different plan.
PLANNING_ENGINE_VERSION = "7.3.2"   # 7.3: workload levels, pins, minimum course level


class EligibilityState(StrEnum):
    """Whether the student may take a course in the planned term."""

    SATISFIED_BY_HISTORY = "satisfied_by_history"      # met by recorded coursework
    CONDITIONAL_ON_PLAN = "conditional_on_plan"        # met only if planned/in-progress pass
    UNSATISFIED = "unsatisfied"                        # not met even then
    NEEDS_CONFIRMATION = "needs_confirmation"          # Phase 6.4 says UNKNOWN - never planned


class OfferingEvidenceKind(StrEnum):
    CONFIRMED_IN_TERM = "confirmed_in_term"                  # SOC data for that term lists it
    NOT_OFFERED_IN_TERM = "not_offered_in_term"   # that term's authoritative data omits it
    # 7.2.4: Rutgers publishes the term, CoursePilot has not validly loaded it:
    # the offering is UNKNOWN - history may NOT decide it.
    TERM_DATA_PENDING = "target_term_data_pending"
    HISTORICAL_SAME_SEASON = "historically_offered_in_season"  # earlier terms of that season
    OTHER_SEASONS_ONLY = "offered_in_other_seasons_only"
    NO_EVIDENCE = "no_offering_evidence"


class OfferingStatus(StrEnum):
    """The confidence a student sees. CONFIRMED is only ever target-term data."""

    CONFIRMED = "confirmed"                                  # the target term lists it
    NOT_FOUND_IN_PUBLISHED_TERM = "not_found_in_published_term"
    HISTORICALLY_LIKELY = "historically_likely"              # unpublished term; same-season history
    INSUFFICIENT_HISTORY = "insufficient_history"            # other seasons only
    UNKNOWN = "unknown"                                      # pending data, or no evidence


def offering_status(kind: OfferingEvidenceKind) -> OfferingStatus:
    return {
        OfferingEvidenceKind.CONFIRMED_IN_TERM: OfferingStatus.CONFIRMED,
        OfferingEvidenceKind.NOT_OFFERED_IN_TERM: OfferingStatus.NOT_FOUND_IN_PUBLISHED_TERM,
        OfferingEvidenceKind.HISTORICAL_SAME_SEASON: OfferingStatus.HISTORICALLY_LIKELY,
        OfferingEvidenceKind.OTHER_SEASONS_ONLY: OfferingStatus.INSUFFICIENT_HISTORY,
        OfferingEvidenceKind.TERM_DATA_PENDING: OfferingStatus.UNKNOWN,
        OfferingEvidenceKind.NO_EVIDENCE: OfferingStatus.UNKNOWN,
    }[kind]


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
    RANKED_BY_REQUIREMENT_COVERAGE = "ranked_by_requirement_coverage"
    #: Phase 7.3: placed, but needs confirmation - the published rule accepts
    #: math placement and the student's profile states none.
    REQUIRES_MATH_PLACEMENT = "requires_math_placement"
    #: Phase 7.3: its prerequisites are met by the student's stated math
    #: placement (Rutgers Mathematics Department placement policy).
    PREREQUISITES_MET_BY_PLACEMENT = "prerequisites_met_by_placement"
    #: Phase 7.3: placed because the student PINNED it; CoursePilot cannot
    #: verify its prerequisites (needs confirmation).
    PREREQUISITES_NEED_CONFIRMATION = "prerequisites_need_confirmation"


class PlanStatus(StrEnum):
    COVERS_ALL_REQUIREMENTS = "covers_all_requirements"   # projected: all met or provisionally
    PARTIAL = "partial"                                   # some requirements could not be planned
    NOTHING_TO_PLAN = "nothing_to_plan"                   # no requirement remains


class Pace(StrEnum):
    """How the student wants the plan shaped (Phase 7.2.1).

    BALANCED is the default. Nothing - not a transcript that puts a student
    ahead, not spare capacity - switches a plan to FINISH_EARLIER except the
    student choosing it.
    """

    BALANCED = "balanced"              # aim for the target; even terms; no stranded last term
    LIGHTER = "lighter"                # never above the target; extra terms are fine
    FINISH_EARLIER = "finish_earlier"  # fill toward the hard maximum to finish sooner


class WorkloadLevel(StrEnum):
    """Per-term INTENSITY (Phase 7.3) - separate from Pace, which is the
    graduation target and sets the horizon. Each level sets a credit floor and
    maximum and a workload budget (app.services.planning.workload.LEVELS).
    CoursePilot planning settings, never Rutgers rules."""

    LIGHT = "light"
    BALANCED = "balanced"
    SEMI_RIGOROUS = "semi_rigorous"
    CHALLENGING = "challenging"


class FillerPreference(StrEnum):
    """How a term's FILLER courses are chosen (Phase 7.3), from SOC meeting modes."""

    FLEXIBLE = "flexible"          # asynchronous / online sections first
    IN_PERSON = "in_person"
    NO_PREFERENCE = "no_preference"


class CoursePin(BaseModel):
    """'I want this course in this term' - a HARD constraint (Phase 7.3)."""

    model_config = ConfigDict(extra="forbid")

    course: str = Field(pattern=r"^\d{2}:\d{3}:\d{3}$")
    term_code: str = Field(pattern=r"^\d{4}[0179]$")


class Strategy(StrEnum):
    """Academic strategy (Phase 7.2.4): a RANKING preference among courses the
    engine has already proved valid. It never relaxes prerequisites, offering
    evidence, the credit maximum or any other hard constraint. "Finish sooner"
    is Pace.FINISH_EARLIER, not a strategy - one setting, one meaning."""

    BALANCED = "balanced"                            # no extra preference
    MAJOR_FIRST = "major_first"                      # primary-major requirements earlier
    REQUIREMENTS_FIRST = "requirements_first"        # school/Core requirements earlier
    UNLOCK_FUTURE_OPTIONS = "unlock_future_options"  # courses many others build on


class PlanningConstraints(BaseModel):
    """Course-load PREFERENCES - CoursePilot planning defaults, NOT Rutgers rules.

    Rutgers publishes credit-load rules per school; CoursePilot does not model
    them, so these numbers are planner settings a student may change and are
    reported as such (`PlanMetadata.constraints_source`, set by the server -
    a client cannot relabel its own numbers as policy).

    Phase 7.2.1:
      * `max_credits_per_term` is a HARD maximum - never exceeded.
      * `target_credits_per_term` is SOFT - what to aim for. It is never a
        minimum: a term may be lighter when prerequisites, offerings or the
        remaining requirements leave less to take.
      * `pace` selects the planner's objective (see Pace). None keeps the
        Phase 6.5 behaviour (fill each term toward the maximum, no levelling)
        for unpersonalized, stateless requests.
    """

    model_config = ConfigDict(extra="forbid")

    max_credits_per_term: Decimal = Field(default=Decimal("15"), ge=1, le=24)
    max_courses_per_term: int = Field(default=5, ge=1, le=8)
    max_terms: int = Field(default=8, ge=1, le=12)
    include_summer: bool = False
    include_winter: bool = False
    target_credits_per_term: Decimal | None = Field(default=None, ge=1, le=24)
    pace: Pace | None = None
    strategy: Strategy = Strategy.BALANCED
    #: Phase 7.3: generic topics courses ("TOPICS IN ...", "TPCS ...") placed
    #: per term. Their content is announced late and varies by term, so one is
    #: the default; fixed-content electives are always preferred first.
    max_topics_courses_per_term: int = Field(default=1, ge=0, le=8)
    #: Phase 7.3: when set, the level's credit floor/maximum and workload
    #: budget shape each term (anchors -> multi-requirement -> major ->
    #: fillers); Pace then only sets the horizon. None keeps the earlier
    #: behaviour (stateless requests, older saved plans).
    workload_level: WorkloadLevel | None = None
    filler_preference: FillerPreference = FillerPreference.NO_PREFERENCE
    pins: list[CoursePin] = Field(default_factory=list, max_length=24)
    #: Phase 7.3: the term the student expects to graduate in; overrides the
    #: graduation target pace (On time / Early / No rush) when set.
    expected_graduation_term: str | None = Field(default=None, pattern=r"^\d{4}[0179]$")

    @model_validator(mode="after")
    def _target_within_max(self) -> "PlanningConstraints":
        if self.target_credits_per_term is not None and \
                self.target_credits_per_term > self.max_credits_per_term:
            raise ValueError("target_credits_per_term cannot exceed max_credits_per_term")
        return self


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
    #: 7.2.4 term authority: ready | published_pending | not_published | unchecked.
    target_term_authority: str = "unchecked"
    #: When CoursePilot last asked Rutgers about the target term (None: never).
    target_term_checked_at: datetime | None = None
    #: Phase 7.3: a FACT, never a probability - "offered in `offered` of the
    #: last `loaded` same-season terms CoursePilot holds data for".
    same_season_offered: int = 0
    same_season_loaded: int = 0

    @property
    def status(self) -> OfferingStatus:
        return offering_status(self.kind)


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


class WorkloadEvidence(BaseModel):
    """A course's workload WEIGHT and every input to it (Phase 7.3) - from
    published SOC facts only; never a difficulty rating."""

    weight: Decimal
    credits: Decimal
    contact_hours: Decimal | None
    has_lab: bool
    level: int
    technical: str | None
    components: dict[str, Decimal]
    delivery: list[str] = Field(default_factory=list)
    basis_term: str | None = None


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
    ranking_evidence: CourseRankingEvidence | None = None
    #: Phase 7.3: a generic topics course. `topics` are the distinct section
    #: subtitles (SOC's topic label) of THIS term's sections - empty when
    #: Rutgers has not announced one (or the term is planned from history).
    is_topics_course: bool = False
    topics: list[str] = Field(default_factory=list)
    #: Phase 7.3 (workload levels): the numbers behind the course's workload
    #: weight, and why it is in this term - anchor | multi_requirement |
    #: major | filler (None without a workload level).
    workload: WorkloadEvidence | None = None
    role: str | None = None
    role_reason: str | None = None


class TermWorkload(BaseModel):
    """Phase 7.3: a term against its workload level - all numbers shown."""

    level: WorkloadLevel
    budget: Decimal
    total: Decimal
    credit_floor: Decimal
    credit_max: Decimal
    contact_hours: Decimal
    notes: list[str] = Field(default_factory=list)


class PlanTerm(BaseModel):
    term_code: str
    label: str
    courses: list[PlannedCourse] = Field(default_factory=list)
    credits: Decimal = Decimal(0)
    workload: TermWorkload | None = None


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


class PlanObservation(BaseModel):
    """A deterministic statement ABOUT the plan, never a change to it (7.2.1).

    `earlier_completion_possible` - the same courses, moved only by moves the
    engine validated, fit in fewer terms at the student's hard maximum. It is
    offered, never applied: only the student's FINISH_EARLIER choice changes
    the objective. It speaks of the CURRENTLY MODELED requirements, not of
    graduation.
    `light_term` - why a term carries less than the target (the final remaining
    requirement, a prerequisite chain, or offering evidence).
    """

    code: str
    message: str
    term_code: str | None = None
    details: dict = Field(default_factory=dict)


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


class GraduationTarget(BaseModel):
    """Phase 7.3: the last term the plan may use, and why (shown on the plan)."""

    term_code: str
    basis: str
    entry_term: str | None = None
    semesters: int | None = None


class PlacementBasis(BaseModel):
    """Phase 7.3: the student's stated math placement this plan relied on."""

    course: str
    title: str | None = None
    source_url: str


class PlanResult(BaseModel):
    target: ScenarioTarget
    status: PlanStatus
    terms: list[PlanTerm] = Field(default_factory=list)
    placement: PlacementBasis | None = None
    graduation_target: GraduationTarget | None = None
    issues: list[PlanningIssue] = Field(default_factory=list)
    requirements: list[RequirementProjection] = Field(default_factory=list)
    assumptions: list[ScenarioAssumption] = Field(default_factory=list)
    observations: list[PlanObservation] = Field(default_factory=list)
    metadata: PlanMetadata

    def canonical_json(self) -> str:
        """Byte-stable serialization (no timestamps exist in the model)."""
        return self.model_dump_json()


__all__ = ["CONSTRAINTS_SOURCE", "PLANNING_ENGINE_VERSION",
           "ConditionalDependency", "EligibilityState",
           "GraduationTarget", "OfferingEvidence", "OfferingEvidenceKind", "PlacementBasis",
           "PlanMetadata",
           "PlanResult", "PlanStatus", "Pace", "PlanObservation", "PlanTerm", "PlannedCourse",
           "PlanningConstraints", "PlanningIssue",
           "PrerequisiteEvidence", "Reason", "RequirementContribution", "RequirementProjection",
           "Severity"]
