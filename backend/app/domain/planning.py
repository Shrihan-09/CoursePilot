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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.course_ranking import CourseRankingEvidence
from app.domain.scenario import ScenarioAssumption, ScenarioTarget

#: Bumped whenever the same inputs could produce a different plan.
PLANNING_ENGINE_VERSION = "7.8.0"   # spread to the graduation target; the degree credit total


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
    #: 10a follow-up: the student chose WHEN to retake a course on their
    #: record (Musts step). Lets the pin place a course already passed with a
    #: grade too low for a later course; a winter/summer pin term is planned
    #: for this pin only (the general winter/summer preference is unchanged).
    retake: bool = False


class GreekLife(StrEnum):
    """Task 2: where the student is in Greek life - a stage, never a chapter."""

    NONE = "none"
    RUSHING = "rushing"
    NEW_MEMBER = "new_member"
    ACTIVE = "active"


class ClubLoad(StrEnum):
    """Task 2: clubs and professional organizations - how much, never which."""

    NONE = "none"
    LIGHT = "light"
    HEAVY = "heavy"


class Commitments(BaseModel):
    """Task 2: what else takes the student's week. Commitment TYPES and hours
    only - no employer, chapter, club or team name is ever stored. Mapped
    deterministically to hours (app.services.planning.questionnaire), which
    lower a term's workload budget. Every field defaults to "none"."""

    model_config = ConfigDict(extra="forbid")

    job_hours_per_week: int = Field(default=0, ge=0, le=40)
    greek_life: GreekLife = GreekLife.NONE
    clubs: ClubLoad = ClubLoad.NONE
    athletics_hours_per_week: int = Field(default=0, ge=0, le=40)
    research_hours_per_week: int = Field(default=0, ge=0, le=40)
    recruiting_hours_per_week: int = Field(default=0, ge=0, le=40)


class TermPlanningOverride(BaseModel):
    """Task 2: the student's answers for ONE semester, replacing the defaults
    there only. A planning setting (an assistant may propose one; the student
    confirms it like any other)."""

    model_config = ConfigDict(extra="forbid")

    term_code: str = Field(pattern=r"^\d{4}[0179]$")
    #: Exact major-related course range for this term (None: the usual one).
    major_courses_min: int | None = Field(default=None, ge=0, le=8)
    major_courses_max: int | None = Field(default=None, ge=0, le=8)
    #: This term's credit range (None: the usual one).
    credit_min: int | None = Field(default=None, ge=1, le=24)
    credit_max: int | None = Field(default=None, ge=1, le=24)
    #: This term's commitments (None: the student's usual ones).
    commitments: Commitments | None = None

    @model_validator(mode="after")
    def _range(self) -> "TermPlanningOverride":
        _check_pair(self.major_courses_min, self.major_courses_max, "major_courses")
        _check_pair(self.credit_min, self.credit_max, "credit")
        return self


def _check_pair(lo: int | None, hi: int | None, name: str) -> None:
    if (lo is None) != (hi is None):
        raise ValueError(f"set both {name}_min and {name}_max, or neither")
    if lo is not None and lo > hi:
        raise ValueError(f"{name}_min cannot be above {name}_max")


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
    #: Explicit student intent: requirement code -> selected child codes.
    #: Omission requests a provisional recommendation. Task 6 owns UI storage.
    alternative_selections: dict[str, list[str]] = Field(default_factory=dict)
    #: Task 2 (workload levels only): weekly commitments lower each term's
    #: workload budget; per-term overrides replace the major-related range and
    #: commitments for one semester. Honors False/None keeps honors-only
    #: courses out of every plan.
    commitments: Commitments = Field(default_factory=Commitments)
    term_overrides: list[TermPlanningOverride] = Field(default_factory=list, max_length=16)
    honors: bool | None = None
    #: Prompt 09: the student's own credit range and major-related range for
    #: every semester (None: the workload level's).
    credit_min: int | None = Field(default=None, ge=1, le=24)
    credit_max: int | None = Field(default=None, ge=1, le=24)
    major_courses_min: int | None = Field(default=None, ge=0, le=8)
    major_courses_max: int | None = Field(default=None, ge=0, le=8)
    #: Correctness fixes item 5: each term takes at least one eligible course in
    #: the major's own subject (01:198 for CS) before a related-department
    #: elective fills the major-related range. Default ON.
    major_subject_each_term: bool = True

    @model_validator(mode="after")
    def _target_within_max(self) -> "PlanningConstraints":
        _check_pair(self.credit_min, self.credit_max, "credit")
        _check_pair(self.major_courses_min, self.major_courses_max, "major_courses")
        if self.target_credits_per_term is not None and \
                self.target_credits_per_term > self.max_credits_per_term:
            raise ValueError("target_credits_per_term cannot exceed max_credits_per_term")
        terms = [o.term_code for o in self.term_overrides]
        if len(terms) != len(set(terms)):
            raise ValueError("one override per semester")
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


class SourceLabel(BaseModel):
    """Phase 7.6: where one fact behind a recommendation came from - the
    section data view's provenance (app.services.sources.sections)."""

    #: offering | prerequisite_rules | core_codes | restrictions | requirement_status
    fact: str
    #: soc_courses_json | degree_navigator_audit | coursepilot_audit
    source: str
    term_code: str | None = None
    data_source_id: str | None = None
    fetched_at: datetime | None = None
    detail: str | None = None


class TracedRequirement(BaseModel):
    """One requirement node a recommendation fills, and what decided that the
    node is open: the student's Degree Navigator audit or CoursePilot's own
    audit ("estimated")."""

    requirement_code: str
    requirement_name: str
    requirement_system: str
    #: degree_navigator | estimated
    status_source: str
    status_rule: str | None = None


class RestrictionCheck(BaseModel):
    """Phase 7.6: the term's section enrollment restrictions against the
    student's known record (app.services.scheduling.candidates decides)."""

    #: unrestricted | satisfied | unknown | not_satisfied | sections_not_published
    outcome: str
    sections: int = 0
    #: Sections whose restrictions allow the student (unrestricted or satisfied).
    open_sections: int = 0
    #: Who the restricted sections are open to: "major 198 (COMPUTER SCIENCE)".
    open_to: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    message: str | None = None


class CoreCodeEvidence(BaseModel):
    """Phase 7.6: the SOC coreCodes a Core contribution rests on."""

    #: The term whose listing was read: the planned term when Rutgers has
    #: published it, else the course's latest loaded offering (labelled).
    term_code: str
    basis: str                     # planned_term | latest_offering
    codes: list[str] = Field(default_factory=list)
    #: goal code -> listed in that term's coreCodes?
    goals: dict[str, bool] = Field(default_factory=dict)


class OfferingPattern(BaseModel):
    """Phase 7.6: how often Rutgers offers the course, from loaded schedules."""

    seasons: list[str] = Field(default_factory=list)       # regular seasons seen
    only_season: str | None = None                          # "Fall" when only Fall
    next_chance: str | None = None                          # the next same-season term
    message: str | None = None


class RecommendationTrace(BaseModel):
    """Phase 7.6: why a course is recommended - every element structured.

    A course fills requirement node(s) itself, or is the prerequisite or
    co-requisite of a course that does (`unlocks_for`). Offering, prerequisite,
    Core-code and restriction evidence and their sources are attached."""

    requirements: list[TracedRequirement] = Field(default_factory=list)
    unlocks_for: list[str] = Field(default_factory=list)
    offering_kind: OfferingEvidenceKind
    offering_term: str
    prerequisite_state: EligibilityState
    prerequisite_rules_term: str | None = None
    core_codes: CoreCodeEvidence | None = None
    restriction: RestrictionCheck | None = None
    pattern: OfferingPattern | None = None
    unlock_chain_depth: int = 0
    sources: list[SourceLabel] = Field(default_factory=list)


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
    #: 10a follow-up: why this planned course is a retake (None: it is not).
    retake: str | None = None
    #: Phase 7.4: plain-language notes on this recommendation - e.g. that the
    #: requirement it counts toward has its status from the student's Degree
    #: Navigator audit, or that DN and CoursePilot disagree (NEEDS REVIEW).
    notes: list[str] = Field(default_factory=list)
    #: Phase 7.6: the structured reason (requirement and its status source,
    #: offering, prerequisite, Core codes, restrictions, provenance).
    trace: RecommendationTrace | None = None


class TermWorkload(BaseModel):
    """Phase 7.3: a term against its workload level - all numbers shown.

    Task 2 adds the major-related course count and range, the commitments
    behind the budget, a "Why this fits you" line and any shortfall reasons."""

    level: WorkloadLevel
    #: The budget this term was planned against (after commitments).
    budget: Decimal
    total: Decimal
    credit_floor: Decimal
    credit_max: Decimal
    contact_hours: Decimal
    notes: list[str] = Field(default_factory=list)
    #: The level's own budget, before commitments.
    base_budget: Decimal | None = None
    commitment_hours: int = 0
    #: Weekly hours left for school: a 60-hour baseline minus commitments.
    hours_available: int | None = None
    #: Courses counting toward the major's requirement tree (a lab taken with
    #: its lecture counts once).
    major_related: int = 0
    major_min: int = 0
    major_max: int = 0
    #: level | override
    major_range_source: str = "level"
    fit: str | None = None
    shortfalls: list[str] = Field(default_factory=list)


class PlanTerm(BaseModel):
    term_code: str
    label: str
    courses: list[PlannedCourse] = Field(default_factory=list)
    #: Credits of the planned courses.
    credits: Decimal = Decimal(0)
    workload: TermWorkload | None = None
    #: Credits of free electives (any degree-credit course the student
    #: chooses) planned toward the degree credit total - never a course.
    free_elective_credits: Decimal = Decimal(0)
    #: Degree credits after this term: record (completed + in progress) plus
    #: every planned course and free-elective credit so far.
    cumulative_credits: Decimal | None = None


class DegreeCredits(BaseModel):
    """The school's degree credit total and where the plan leaves it."""

    required: Decimal
    #: Completed credits that apply to the degree, and in-progress credits.
    completed: Decimal
    in_progress: Decimal
    planned_courses: Decimal
    planned_free_electives: Decimal
    total: Decimal
    met: bool
    source_title: str
    source_url: str
    source_quote: str
    retrieved_at: str


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


class RequirementAlternative(BaseModel):
    code: str
    name: str
    estimated_terms: int | None = None


class AlternativeChoice(BaseModel):
    """One one-of requirement's commitment in a plan (task 1).

    `status`:
      * undecided          - no student choice, and no alternative can be
                             completed within the plan's horizon;
      * planner_recommended - no student choice; `chosen` is the planner's
                             recommendation (`recommendation_reason` says why);
      * student_selected   - `chosen` is the student's explicit choice;
      * needs_reselection  - the student's choice cannot be completed in this
                             plan; it is HELD (never switched) and the student
                             must choose again.
    """

    requirement_code: str
    status: Literal["undecided", "planner_recommended", "student_selected",
                    "needs_reselection"] = "planner_recommended"
    #: What the plan commits to (and closes every competitor of).
    chosen: list[RequirementAlternative]
    #: The planner's recommendation, also when the student chose otherwise.
    recommended: list[RequirementAlternative] = Field(default_factory=list)
    recommendation_reason: str | None = None
    #: The student's explicit choice (codes), as requested.
    selected: list[str] = Field(default_factory=list)
    #: Chosen codes that cannot be completed in this plan.
    unavailable: list[str] = Field(default_factory=list)
    alternatives: list[RequirementAlternative]
    #: Planner recommendations replaced during construction (never a student's).
    fallback_from: list[str] = Field(default_factory=list)


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
    alternative_choices: list[AlternativeChoice] = Field(default_factory=list)


class GraduationTarget(BaseModel):
    """Phase 7.3: the last term the plan may use, and why (shown on the plan)."""

    term_code: str
    basis: str
    entry_term: str | None = None
    semesters: int | None = None
    #: Correctness fixes (item 1): the earliest Fall/Spring term on the record
    #: and, when it precedes the stated first semester, what was preferred.
    record_first_term: str | None = None
    conflict: str | None = None


class PlacementBasis(BaseModel):
    """Phase 7.3: the student's stated math placement this plan relied on."""

    course: str
    title: str | None = None
    source_url: str


class DeclarationSource(BaseModel):
    id: str
    title: str
    url: str
    kind: str
    retrieved_at: str


class DeclarationRequirement(BaseModel):
    """One published condition for declaring a major, with its citations."""

    code: str
    text: str
    #: ids of `Declaration.sources` that state it (verbatim quotes on file)
    sources: list[str] = Field(default_factory=list)
    #: courses_min_grade only: course -> met | in_progress | below_minimum |
    #: not_taken | unknown, read from the student's own record
    courses: dict[str, str] = Field(default_factory=dict)
    min_grade: str | None = None
    #: met | not_met | in_progress | not_evaluated (a statement, not checkable)
    status: str = "not_evaluated"


class Declaration(BaseModel):
    """Phase 7.6: a student planning toward a major they have not declared."""

    status: str                    # undeclared
    program_key: str
    #: "Planning toward Computer Science B.S. (not yet declared)"
    label: str
    requirements: list[DeclarationRequirement] = Field(default_factory=list)
    sources: list[DeclarationSource] = Field(default_factory=list)
    #: Where the official sources disagree - shown, never resolved.
    conflicts: list[str] = Field(default_factory=list)
    #: Set when no official source CoursePilot holds states the requirements.
    message: str | None = None


class SchoolCoreStatus(BaseModel):
    """Phase 7.6: which school Core applies, chosen by the program's school."""

    school_code: str
    #: encoded | unknown
    status: str
    requirement_sets: list[str] = Field(default_factory=list)
    message: str


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
    #: Spread task: None when the program's school/catalog year has no
    #: modelled total (an issue says so).
    degree_credits: DegreeCredits | None = None
    #: Phase 7.6
    school_core: SchoolCoreStatus | None = None
    declaration: Declaration | None = None

    def canonical_json(self) -> str:
        """Byte-stable serialization (no timestamps exist in the model)."""
        return self.model_dump_json()


__all__ = ["CONSTRAINTS_SOURCE", "PLANNING_ENGINE_VERSION",
           "ConditionalDependency", "EligibilityState",
           "GraduationTarget", "OfferingEvidence", "OfferingEvidenceKind", "PlacementBasis",
           "PlanMetadata",
           "PlanResult", "PlanStatus", "Pace", "PlanObservation", "PlanTerm", "PlannedCourse",
           "PlanningConstraints", "PlanningIssue", "CoreCodeEvidence", "ClubLoad", "Commitments",
           "GreekLife", "TermPlanningOverride", "Declaration",
           "DeclarationRequirement", "DegreeCredits", "DeclarationSource", "OfferingPattern",
           "RecommendationTrace", "RestrictionCheck", "SchoolCoreStatus", "SourceLabel",
           "TracedRequirement",
           "PrerequisiteEvidence", "Reason", "RequirementContribution", "RequirementProjection",
           "Severity"]
