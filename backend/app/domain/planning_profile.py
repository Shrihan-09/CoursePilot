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

from app.domain.planning import Pace, PlanningConstraints

_HHMM = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
_PROGRAM_KEY = r"^[a-z0-9]+-[a-z0-9]+-[a-z0-9]+(-[a-z0-9]+)*$"

#: The horizon a pace implies when the student does not set one. A lighter
#: pace needs more terms to reach the same requirements (persona audit N4).
DEFAULT_MAX_TERMS: dict[Pace, int] = {Pace.BALANCED: 8, Pace.LIGHTER: 12, Pace.FINISH_EARLIER: 8}


class ScheduleDefaults(BaseModel):
    """Defaults the Schedule page starts from. They are HARD limits in the
    Schedule Engine (a section outside them is never used), and the page says
    so; the engine's ranking already prefers fewer class days and smaller gaps."""

    model_config = ConfigDict(extra="forbid")

    earliest_start: str | None = Field(default=None, pattern=_HHMM)
    latest_end: str | None = Field(default=None, pattern=_HHMM)
    avoid_days: list[Literal["M", "T", "W", "H", "F", "S", "U"]] = Field(default_factory=list,
                                                                        max_length=7)

    @model_validator(mode="after")
    def _window(self) -> "ScheduleDefaults":
        if self.earliest_start and self.latest_end and self.earliest_start >= self.latest_end:
            raise ValueError("earliest_start must be before latest_end")
        return self


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
    target_credits_per_term: Decimal = Field(default=Decimal("15"), ge=1, le=24)
    max_credits_per_term: Decimal = Field(default=Decimal("18"), ge=1, le=24)
    max_courses_per_term: int = Field(default=6, ge=1, le=8)
    max_terms: int | None = Field(default=None, ge=1, le=12)
    include_summer: bool = False
    include_winter: bool = False
    schedule: ScheduleDefaults = Field(default_factory=ScheduleDefaults)
    additional_goals: list[AdditionalGoal] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _load(self) -> "PlanningProfileInput":
        if self.target_credits_per_term > self.max_credits_per_term:
            raise ValueError("The target load cannot be higher than the maximum load.")
        return self


def to_constraints(profile: PlanningProfileInput) -> PlanningConstraints:
    """The ONLY path from preferences to the planner."""
    return PlanningConstraints(
        max_credits_per_term=profile.max_credits_per_term,
        max_courses_per_term=profile.max_courses_per_term,
        max_terms=profile.max_terms or DEFAULT_MAX_TERMS[profile.pace],
        include_summer=profile.include_summer,
        include_winter=profile.include_winter,
        target_credits_per_term=profile.target_credits_per_term,
        pace=profile.pace)


__all__ = ["AdditionalGoal", "DEFAULT_MAX_TERMS", "PlanningProfileInput", "ScheduleDefaults",
           "to_constraints"]
