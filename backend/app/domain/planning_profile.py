"""The Planning Profile: what the student WANTS (Phase 7.2.1; DATA_MODEL section 49).

CoursePilot keeps two kinds of student information strictly apart:

    ACADEMIC CONTEXT   what is TRUE   - confirmed history, program, the engines' results
    PLANNING PROFILE   what is WANTED - pace, credit load, seasons, schedule defaults, goals

Nothing in a profile is ever evidence about the student's record, and nothing
in the record is ever inferred from a profile. A profile changes only by the
student CONFIRMING one (PUT /student/planning-profile); a future assistant may
PROPOSE the same structure, which the server validates exactly like a form.

The planner reads the confirmed profile through `to_constraints` - never chat.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.modality import ModalityPreferences
from app.domain.planning import (
    Commitments,
    CoursePin,
    FillerPreference,
    Pace,
    PlanningConstraints,
    Strategy,
    TermPlanningOverride,
    WorkloadLevel,
)
from app.domain.schedule import BlockedTime

_HHMM = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
_PROGRAM_KEY = r"^[a-z0-9]+-[a-z0-9]+-[a-z0-9]+(-[a-z0-9]+)*$"

#: The horizon a pace implies when the student does not set one. A lighter
#: pace needs more terms to reach the same requirements (persona audit N4).
DEFAULT_MAX_TERMS: dict[Pace, int] = {Pace.BALANCED: 8, Pace.LIGHTER: 12, Pace.FINISH_EARLIER: 8}
#: Phase 7.3: with a workload level, Pace is ONLY the graduation target and
#: sets the horizon - "On time" (balanced) 8 terms, "Early" (finish_earlier)
#: 7, "No rush" (lighter) 12. The level, not the pace, sets each term's load.
GRADUATION_TARGET_TERMS: dict[Pace, int] = {Pace.BALANCED: 8, Pace.FINISH_EARLIER: 7,
                                            Pace.LIGHTER: 12}


class ScheduleDefaults(BaseModel):
    """Defaults the Schedule page starts from. They are HARD limits in the
    Schedule Engine (a section outside them is never used), and the page says
    so; the engine's ranking already prefers fewer class days and smaller gaps."""

    model_config = ConfigDict(extra="forbid")

    modality: ModalityPreferences = Field(default_factory=ModalityPreferences,
        description="Proposal-only foundation; not applied by planning or scheduling yet.")
    earliest_start: str | None = Field(default=None, pattern=_HHMM)
    latest_end: str | None = Field(default=None, pattern=_HHMM)
    avoid_days: list[Literal["M", "T", "W", "H", "F", "S", "U"]] = Field(default_factory=list,
                                                                        max_length=7)
    #: Task 2: fixed weekly blocks (job shifts, club meetings) - hard limits too.
    blocked_times: list[BlockedTime] = Field(default_factory=list, max_length=20)
    #: Prompt 09: preferred campuses (SOC codes: BUS, CAC, LIV, D/C, DNB). They
    #: order schedule options; no section is excluded for its campus.
    preferred_campuses: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _window(self) -> "ScheduleDefaults":
        if self.earliest_start and self.latest_end and self.earliest_start >= self.latest_end:
            raise ValueError("earliest_start must be before latest_end")
        return self


class TermAnswers(TermPlanningOverride):
    """Task 2: the questionnaire's answers for one semester. The planner reads
    the range and commitments (TermPlanningOverride); `blocked_times` replace
    the usual fixed blocks when that semester's schedule is built."""

    blocked_times: list[BlockedTime] | None = Field(default=None, max_length=20)


class AdditionalGoal(BaseModel):
    """An INTENT, e.g. "exploring a Math minor". It is recorded, shown and never
    planned for: CoursePilot does not yet combine programs (section 49.8)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["second_major", "minor", "certificate"]
    program_key: str | None = Field(default=None, pattern=_PROGRAM_KEY, max_length=96)
    label: str = Field(min_length=1, max_length=120)
    status: Literal["exploring"] = "exploring"

    @field_validator("label")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("label is required")
        return v


class PlanningProfileInput(BaseModel):
    """What a student confirms. Every field is a CoursePilot setting, not Rutgers policy."""

    model_config = ConfigDict(extra="forbid")

    pace: Pace = Pace.BALANCED
    strategy: Strategy = Strategy.BALANCED
    target_credits_per_term: Decimal = Field(default=Decimal("15"), ge=1, le=24)
    max_credits_per_term: Decimal = Field(default=Decimal("18"), ge=1, le=24)
    max_courses_per_term: int = Field(default=6, ge=1, le=8)
    max_terms: int | None = Field(default=None, ge=1, le=12)
    include_summer: bool = False
    include_winter: bool = False
    schedule: ScheduleDefaults = Field(default_factory=ScheduleDefaults)
    additional_goals: list[AdditionalGoal] = Field(default_factory=list, max_length=4)
    #: Phase 7.3 - per-term intensity, filler preference and pinned courses.
    #: A profile confirmed before 7.3 has no level and keeps the pace behaviour.
    workload_level: WorkloadLevel | None = None
    filler_preference: FillerPreference = FillerPreference.NO_PREFERENCE
    pins: list[CoursePin] = Field(default_factory=list, max_length=24)
    #: Optional: overrides the graduation target pace (e.g. "20281" Spring 2028).
    expected_graduation_term: str | None = Field(default=None, pattern=r"^\d{4}[0179]$")
    #: Task 2 planning questionnaire. Every answer is optional: the defaults
    #: (no commitments, not in an honors program, the level's major-related
    #: range) are what a skipped question means.
    commitments: Commitments = Field(default_factory=Commitments)
    #: True: in an honors program (honors-only courses may be planned, still
    #: needs confirmation). False or None (skipped): never planned.
    honors: bool | None = None
    term_overrides: list[TermAnswers] = Field(default_factory=list, max_length=16)
    #: Prompt 09: "How many credits?" and "How many major courses?" for every
    #: semester (None: the workload level's range).
    credit_min: int | None = Field(default=None, ge=1, le=24)
    credit_max: int | None = Field(default=None, ge=1, le=24)
    major_courses_min: int | None = Field(default=None, ge=0, le=8)
    major_courses_max: int | None = Field(default=None, ge=0, le=8)
    #: Correctness fixes item 5: at least one course in the major's own subject
    #: each term (when eligible). Default ON.
    major_subject_each_term: bool = True

    @model_validator(mode="after")
    def _load(self) -> "PlanningProfileInput":
        if self.target_credits_per_term > self.max_credits_per_term:
            raise ValueError("The target load cannot be higher than the maximum load.")
        terms = [o.term_code for o in self.term_overrides]
        if len(terms) != len(set(terms)):
            raise ValueError("Only one set of answers per semester.")
        from app.domain.planning import _check_pair
        _check_pair(self.credit_min, self.credit_max, "credit")
        _check_pair(self.major_courses_min, self.major_courses_max, "major_courses")
        return self


def credit_range(profile: PlanningProfileInput) -> tuple[Decimal, Decimal] | None:
    """Correctness fixes item 6: with a workload level, ONE credit range rules
    everywhere - the student's own range if set, else the level's (Semi-rigorous
    16-19). The older target/maximum fields do not apply then. None: no level
    (the target/maximum are the range)."""
    if profile.workload_level is None:
        return None
    if profile.credit_min is not None and profile.credit_max is not None:
        return Decimal(profile.credit_min), Decimal(profile.credit_max)
    from app.services.planning.workload import LEVELS

    floor, cap, _ = LEVELS[profile.workload_level]
    return floor, cap


def to_constraints(profile: PlanningProfileInput) -> PlanningConstraints:
    """The ONLY path from preferences to the planner."""
    horizon = (GRADUATION_TARGET_TERMS if profile.workload_level else DEFAULT_MAX_TERMS)
    target, maximum = credit_range(profile) or (profile.target_credits_per_term,
                                               profile.max_credits_per_term)
    return PlanningConstraints(
        max_credits_per_term=maximum,
        max_courses_per_term=profile.max_courses_per_term,
        max_terms=profile.max_terms or horizon[profile.pace],
        include_summer=profile.include_summer,
        include_winter=profile.include_winter,
        target_credits_per_term=target,
        pace=profile.pace,
        strategy=profile.strategy,
        workload_level=profile.workload_level,
        filler_preference=profile.filler_preference,
        pins=profile.pins,
        expected_graduation_term=profile.expected_graduation_term,
        commitments=profile.commitments,
        honors=profile.honors,
        credit_min=profile.credit_min,
        credit_max=profile.credit_max,
        major_courses_min=profile.major_courses_min,
        major_courses_max=profile.major_courses_max,
        major_subject_each_term=profile.major_subject_each_term,
        # Fixed blocks shape SCHEDULES, not which courses: kept out of the
        # plan's constraints so editing a shift does not make a plan stale.
        term_overrides=[TermPlanningOverride.model_validate(
                            o.model_dump(exclude={"blocked_times"}))
                        for o in profile.term_overrides
                        if o.major_courses_min is not None or o.credit_min is not None
                        or o.commitments is not None])


__all__ = ["AdditionalGoal", "DEFAULT_MAX_TERMS", "GRADUATION_TARGET_TERMS", "credit_range",
           "PlanningProfileInput",
           "ScheduleDefaults", "TermAnswers", "to_constraints"]
