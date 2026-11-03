"""Planning questionnaire mappings (Task 2): answers -> numbers the planner uses.

Every mapping is a fixed table - the same answers always give the same plan -
and every number is shown to the student next to the term it shaped. These
are CoursePilot planning settings, never Rutgers rules.

MAJOR-RELATED COURSES PER TERM. A course is major-related when it counts
toward the major's requirement tree, including the major's required math and
science (Calculus, a B.S. science sequence) - not a Core-only course or a
general elective. The default range comes from the workload level:

    Light 1-2   Balanced 2-3   Semi-rigorous 3   Challenging 3-4

A student may set an exact range for any one semester (`term_overrides`).

A student may also choose a range for every semester (`major_courses_min/
max`); a semester's own answer wins over it, and it wins over the level.

CREDITS. The level sets the credit floor and maximum unless the student sets
their own (`credit_min/max`, every semester or one). The floor is HARD.

COMMITMENTS -> HOURS -> BUDGET (Prompt 09 section 3). A student has a
baseline of 60 weekly hours for school; commitments come off it. Stated hours
count as stated; Greek life and clubs are stages/amounts mapped to typical
weekly hours below. A term's workload budget drops by one point per 3 weekly
hours of commitments (one credit is about 3 hours of class and study a week),
rounded to the nearest half point, and never below the credit floor.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.domain.planning import (
    ClubLoad,
    Commitments,
    GreekLife,
    PlanningConstraints,
    WorkloadLevel,
)

#: (min, max) major-related courses per term, by workload level.
MAJOR_RANGE: dict[WorkloadLevel, tuple[int, int]] = {
    WorkloadLevel.LIGHT: (1, 2),
    WorkloadLevel.BALANCED: (2, 3),
    WorkloadLevel.SEMI_RIGOROUS: (3, 3),
    WorkloadLevel.CHALLENGING: (3, 4),
}

#: Typical weekly hours by stage. Rushing and the new-member period are the
#: busiest; an active member's chapter meeting and events are fewer.
GREEK_HOURS: dict[GreekLife, int] = {GreekLife.NONE: 0, GreekLife.RUSHING: 6,
                                     GreekLife.NEW_MEMBER: 10, GreekLife.ACTIVE: 4}
CLUB_HOURS: dict[ClubLoad, int] = {ClubLoad.NONE: 0, ClubLoad.LIGHT: 3, ClubLoad.HEAVY: 8}
#: Prompt 09 s4 fill order: courses filling 2+ requirements BEFORE the
#: major-related courses (keeping room for the major minimum). False puts
#: every major-related course first and other double-counters after them -
#: measured in the Task 2 PR; the owner decides.
MULTI_BEFORE_MAJOR = True
#: Weekly hours of class and study per workload point (credit).
HOURS_PER_POINT = 3
#: Weekly hours a student has for school before any commitment.
HOURS_BASELINE = 60

_LABELS = {"job": "job", "greek_life": "Greek life", "clubs": "clubs",
           "athletics": "athletics", "research": "research", "recruiting": "recruiting"}


def commitment_hours(c: Commitments) -> dict[str, int]:
    """Weekly hours per commitment type (only the non-zero ones), in a fixed order."""
    hours = {"job": c.job_hours_per_week, "greek_life": GREEK_HOURS[c.greek_life],
             "clubs": CLUB_HOURS[c.clubs], "athletics": c.athletics_hours_per_week,
             "research": c.research_hours_per_week, "recruiting": c.recruiting_hours_per_week}
    return {k: v for k, v in hours.items() if v}


def budget_reduction(hours: int) -> Decimal:
    """Workload points the commitments take: hours / 3, to the nearest half."""
    halves = (Decimal(hours) * 2 / HOURS_PER_POINT).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return halves / 2


def describe_hours(by_type: dict[str, int]) -> str:
    """'15 hours a week: job 10, clubs 3, Greek life 2' / '10 hours a week of job'."""
    total = sum(by_type.values())
    if len(by_type) == 1:
        return f"{total} hours a week of {_LABELS[next(iter(by_type))]}"
    return f"{total} hours a week: " + ", ".join(f"{_LABELS[k]} {v}" for k, v in by_type.items())


class TermSettings:
    """The questionnaire's effect on ONE term: the major-related range and its
    source, and the commitment-adjusted budget."""

    def __init__(self, constraints: PlanningConstraints, term: str,
                 floor: Decimal | None = None, base_budget: Decimal | None = None) -> None:
        from app.services.planning.workload import LEVELS

        c = constraints
        override = next((o for o in c.term_overrides if o.term_code == term), None)
        level = c.workload_level or WorkloadLevel.BALANCED
        level_floor, level_cap, level_budget = LEVELS[level]
        # Credits: this semester's answer, else the student's own, else the level's.
        self.floor, self.cap, self.credit_source = level_floor, level_cap, "level"
        if c.credit_min is not None:
            self.floor, self.cap = Decimal(c.credit_min), Decimal(c.credit_max)
            self.credit_source = "profile"
        if override is not None and override.credit_min is not None:
            self.floor, self.cap = Decimal(override.credit_min), Decimal(override.credit_max)
            self.credit_source = "override"
        if floor is not None:
            self.floor = floor
        base_budget = level_budget if base_budget is None else base_budget
        floor = self.floor
        # Major-related range: the same precedence.
        self.major_min, self.major_max = MAJOR_RANGE[level]
        self.source = "level"
        if c.major_courses_min is not None:
            self.major_min, self.major_max = c.major_courses_min, c.major_courses_max
            self.source = "profile"
        if override is not None and override.major_courses_min is not None:
            self.major_min, self.major_max = override.major_courses_min, override.major_courses_max
            self.source = "override"
        commitments = (override.commitments if override is not None
                       and override.commitments is not None else c.commitments)
        self.commitments_from_override = override is not None and override.commitments is not None
        self.hours_by_type = commitment_hours(commitments)
        self.hours = sum(self.hours_by_type.values())
        self.hours_available = max(HOURS_BASELINE - self.hours, 0)
        #: Greek life stage of this term, for the note ("your rush semester ...").
        self.greek = commitments.greek_life
        self.base_budget = base_budget
        reduced = base_budget - budget_reduction(self.hours)
        self.budget = max(reduced, floor)
        #: The commitments would take the budget below the credit floor.
        self.clamped = reduced < floor

    def range_text(self) -> str:
        return (str(self.major_min) if self.major_min == self.major_max
                else f"{self.major_min}-{self.major_max}")


def pace_level_conflict(constraints: PlanningConstraints) -> str | None:
    """A graduation target and workload level that pull in opposite directions
    (Pace stays separate from the level; this only warns)."""
    from app.domain.planning import Pace
    c = constraints
    if c.workload_level is None or c.pace is None:
        return None
    if c.pace is Pace.FINISH_EARLIER and c.workload_level is WorkloadLevel.LIGHT:
        return ("You chose to graduate early with Light semesters. Light semesters carry 12-14 "
                "credits, so finishing early may not fit; the plan will say if it does not.")
    if c.pace is Pace.LIGHTER and c.workload_level is WorkloadLevel.CHALLENGING:
        return ("You chose No rush with Challenging semesters. Challenging semesters carry 17-21 "
                "credits, so your plan may finish well before your target.")
    return None


__all__ = ["CLUB_HOURS", "GREEK_HOURS", "HOURS_BASELINE", "HOURS_PER_POINT", "MAJOR_RANGE",
           "MULTI_BEFORE_MAJOR",
           "TermSettings",
           "budget_reduction", "commitment_hours", "describe_hours", "pace_level_conflict"]
