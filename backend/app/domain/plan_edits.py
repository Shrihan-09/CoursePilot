"""Plan changes the STUDENT makes (Prompt 11 Part A): options, swaps, locks, undo.

```
the student picks      Options sheet / "Your choices" / search      (app.services.planning.options)
the engine proposes    preview = the planner re-run with the change (app.services.planning.edits)
the student confirms   apply (only if the plan is unchanged since)   -> saved, undoable
```

Nothing here changes a plan by itself. A preview is a PROPOSED change and is
never saved as the plan; only `apply` with the preview's id writes, and only
when the saved plan's revision still equals the one the preview was computed
from. A future assistant may build a `PlanChange` from a sentence - the same
preview and the same confirmation apply.

## Course states

```
LOCKED            the student locked it (or it is an anchor: a Musts pin, a needed retake)
USER_SELECTED     the student picked it from its options
PLANNER_SELECTED  the planner chose it
PROPOSED_CHANGE   in a preview only - never saved
```

The planner never replaces a LOCKED or USER_SELECTED course: they are pins
(`CoursePin.source` "lock" / "selection"). When one can no longer be placed
the plan says so (`needs_reselection`) and keeps the student's choice.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.planning import CourseAvoid, CoursePin

COURSE = r"^\d{2}:\d{3}:\d{3}$"
TERM = r"^\d{4}[0179]$"


class CourseState(StrEnum):
    LOCKED = "locked"
    USER_SELECTED = "user_selected"
    PLANNER_SELECTED = "planner_selected"
    PROPOSED_CHANGE = "proposed_change"


class TermCap(BaseModel):
    """"Lighten this semester": the term's credit range, lowered by the student."""

    model_config = ConfigDict(extra="forbid")

    term_code: str = Field(pattern=TERM)
    credit_min: int = Field(ge=1, le=24)
    credit_max: int = Field(ge=1, le=24)


class PlanEdits(BaseModel):
    """The student's own changes to their plan - stored with the saved plan,
    carried into every regeneration, never invented by the planner."""

    model_config = ConfigDict(extra="forbid")

    locks: list[CoursePin] = Field(default_factory=list, max_length=48)
    selections: list[CoursePin] = Field(default_factory=list, max_length=24)
    avoid: list[CourseAvoid] = Field(default_factory=list, max_length=48)
    #: Semesters the student locked whole (each of their courses is in `locks`).
    locked_terms: list[str] = Field(default_factory=list, max_length=16)
    term_caps: list[TermCap] = Field(default_factory=list, max_length=16)
    #: One-of requirements the student decided ("Physics or Chemistry?"):
    #: requirement code -> chosen child codes (PlanningConstraints semantics).
    alternative_selections: dict[str, list[str]] = Field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.locks or self.selections or self.avoid or self.locked_terms
                    or self.term_caps or self.alternative_selections)


ChangeKind = Literal["swap", "move", "remove", "add", "lock", "unlock", "lock_term",
                     "unlock_term", "lighten_term", "choose_alternative"]


class PlanChange(BaseModel):
    """One change the student asked for. Validated per kind."""

    model_config = ConfigDict(extra="forbid")

    kind: ChangeKind
    course: str | None = Field(default=None, pattern=COURSE)
    #: The term the course is in (every kind but choose_alternative).
    term_code: str | None = Field(default=None, pattern=TERM)
    #: swap: the course that replaces `course`; add: unused.
    replacement: str | None = Field(default=None, pattern=COURSE)
    #: move: the term `course` goes to.
    to_term: str | None = Field(default=None, pattern=TERM)
    #: choose_alternative: the one-of requirement and the children chosen.
    requirement_code: str | None = Field(default=None, max_length=64)
    alternatives: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _fields(self) -> PlanChange:
        if self.kind == "choose_alternative":
            if not self.requirement_code or not self.alternatives:
                raise ValueError("choose_alternative names the requirement and its choice")
            return self
        if not self.term_code:
            raise ValueError(f"a {self.kind} names its term")
        needs_course = self.kind in ("swap", "move", "remove", "add", "lock", "unlock")
        if needs_course and not self.course:
            raise ValueError(f"a {self.kind} names a course")
        if self.kind == "swap" and not self.replacement:
            raise ValueError("a swap names its replacement")
        if self.kind == "move" and not self.to_term:
            raise ValueError("a move names the term it goes to")
        if self.kind == "move" and self.to_term == self.term_code:
            raise ValueError("a move goes to a different term")
        return self


class CourseLine(BaseModel):
    course: str
    title: str
    credits: Decimal
    state: CourseState = CourseState.PLANNER_SELECTED


class TermDiff(BaseModel):
    term_code: str
    label: str
    before: list[CourseLine] = Field(default_factory=list)
    after: list[CourseLine] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    credits_before: Decimal = Decimal(0)
    credits_after: Decimal = Decimal(0)
    workload_before: Decimal | None = None
    workload_after: Decimal | None = None
    budget: Decimal | None = None


class ForcedMove(BaseModel):
    """A course the student did NOT touch whose term changed - always listed."""

    course: str
    title: str
    from_term: str | None          # None: newly planned
    to_term: str | None            # None: no longer planned
    reason: str


class PlanDiff(BaseModel):
    terms: list[TermDiff] = Field(default_factory=list)
    forced_moves: list[ForcedMove] = Field(default_factory=list)
    graduation_before: str | None = None
    graduation_after: str | None = None
    last_term_before: str | None = None
    last_term_after: str | None = None
    #: Requirements the plan no longer completes / now completes.
    requirements_lost: list[str] = Field(default_factory=list)
    requirements_gained: list[str] = Field(default_factory=list)
    #: New blocking/needs-confirmation issues the change introduces (messages).
    new_issues: list[str] = Field(default_factory=list)
    #: One line: "01:082:105 out of Spring 2027; ...; graduation unchanged".
    summary: str = ""
    #: True when the change moves another course, delays graduation, or loses
    #: a requirement - a UI asks for one more tap.
    big: bool = False


class PlanPreview(BaseModel):
    preview_id: str
    base_revision: int
    change: PlanChange
    valid: bool
    #: Why the change cannot be made (closed codes with a message each).
    errors: list[str] = Field(default_factory=list)
    diff: PlanDiff | None = None
    expires_at: datetime
    #: Always False: a preview is never the plan.
    applied: bool = False


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: str = Field(min_length=1, max_length=64)


class PlannedCourseState(BaseModel):
    course: str
    term_code: str
    state: CourseState
    #: anchor (a Musts pin or a needed retake) | locked | user_selected | None
    locked_reason: str | None = None


__all__ = ["ApplyRequest", "ChangeKind", "CourseLine", "CourseState", "ForcedMove",
           "PlanChange", "PlanDiff", "PlanEdits", "PlanPreview", "PlannedCourseState",
           "TermCap", "TermDiff"]
