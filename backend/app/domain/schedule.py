"""Schedule Engine domain (Phase 6.6): actual Rutgers sections for ONE term.

```
Planning Engine   "which courses, which term?"         app.services.planning   (6.5)
Schedule Engine   "which sections of THOSE courses?"   app.services.scheduling (this phase)
```

The Schedule Engine never chooses courses. It receives exact course codes
and either returns section combinations for exactly those courses, or says
structurally why it cannot. Every fact a later explanation layer needs is a
field here: an explanation reads these, it never re-derives them.

Vocabulary is deliberate:

  * options are "ranked schedule options" under a stated objective, never
    "the best schedule";
  * structural validity (meetings, components, restrictions) and current
    registration availability (open / closed) are separate fields - a closed
    section can still prove a valid schedule exists;
  * availability is labelled with where it came from and when it was seen;
    an archived snapshot is never presented as live.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Bumped whenever the same inputs could produce different options.
SCHEDULE_ENGINE_VERSION = "6.6.0"

#: SOC weekday codes (H = Thursday, U = Sunday), in calendar order.
WEEKDAYS = ("M", "T", "W", "H", "F", "S", "U")


class MeetingKind(StrEnum):
    TIMED = "timed"                    # a weekday and a start/end time
    ASYNCHRONOUS = "asynchronous"      # online, no meeting time by design
    ARRANGED = "arranged"              # by arrangement (research, independent study...)
    TBA = "tba"                        # a class meeting whose time is not published
    MALFORMED = "malformed"            # published, but not a usable interval


#: Kinds whose time is not known. They are never treated as free time.
TIME_UNKNOWN_KINDS = (MeetingKind.ARRANGED, MeetingKind.TBA, MeetingKind.MALFORMED)


class RestrictionOutcome(StrEnum):
    UNRESTRICTED = "unrestricted"      # SOC lists no "open to" restriction
    SATISFIED = "satisfied"            # the student's known record matches a listed entry
    NOT_SATISFIED = "not_satisfied"    # definitely excluded (needs complete student attributes)
    UNKNOWN = "unknown"                # CoursePilot cannot tell - never treated as eligible


class AvailabilityState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class AvailabilityFreshness(StrEnum):
    LIVE = "live"                      # observed recently enough to say "right now"
    ARCHIVED = "archived_snapshot"     # a past SOC download - historical evidence only


class ScheduleStatus(StrEnum):
    OPTIONS_FOUND = "options_found"
    NO_VALID_SCHEDULE = "no_valid_schedule"
    TERM_SCHEDULE_NOT_PUBLISHED = "term_schedule_not_published"
    COURSE_ELIGIBILITY_FAILED = "course_eligibility_failed"
    SEARCH_LIMIT_REACHED = "search_limit_reached"   # stopped before any option was proven


class Severity(StrEnum):
    BLOCKER = "blocker"
    NEEDS_CONFIRMATION = "needs_confirmation"
    WARNING = "warning"


class Reason(StrEnum):
    NO_TIME_CONFLICTS = "no_time_conflicts"
    ALL_MEETING_TIMES_VERIFIED = "all_meeting_times_verified"
    ALL_HARD_CONSTRAINTS_SATISFIED = "all_hard_constraints_satisfied"
    ALL_RESTRICTIONS_SATISFIED_OR_ABSENT = "all_restrictions_satisfied_or_absent"
    ALL_SECTIONS_OPEN_IN_SNAPSHOT = "all_sections_open_in_snapshot"
    REQUIRED_COMPONENTS_INCLUDED = "required_components_included"
    MATCHES_PREFERRED_CAMPUS = "matches_preferred_campus"


# --------------------------------------------------------------------------
# request side
# --------------------------------------------------------------------------

_HHMM = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"


class SchedulePreferences(BaseModel):
    """Student scheduling settings - CoursePilot settings, NOT Rutgers rules.

    HARD constraints (a section that violates one is never scheduled):
      earliest_start, latest_end, avoid_days, minimum_minutes_between_classes.
    SOFT preference (only orders the options):
      preferred_campuses.
    """

    model_config = ConfigDict(extra="forbid")

    earliest_start: str | None = Field(default=None, pattern=_HHMM)
    latest_end: str | None = Field(default=None, pattern=_HHMM)
    avoid_days: list[str] = Field(default_factory=list, max_length=7)
    minimum_minutes_between_classes: int = Field(default=0, ge=0, le=120)
    preferred_campuses: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("avoid_days")
    @classmethod
    def _days(cls, v: list[str]) -> list[str]:
        bad = [d for d in v if d not in WEEKDAYS]
        if bad:
            raise ValueError(f"unknown weekday codes {bad}; use {list(WEEKDAYS)}")
        return sorted(set(v), key=WEEKDAYS.index)

    @field_validator("preferred_campuses")
    @classmethod
    def _campuses(cls, v: list[str]) -> list[str]:
        return sorted({c.strip().upper() for c in v if c.strip()})

    def hard_constraints(self) -> list[str]:
        """Names of the hard constraints this request actually sets."""
        out = []
        if self.earliest_start:
            out.append("earliest_start")
        if self.latest_end:
            out.append("latest_end")
        if self.avoid_days:
            out.append("avoid_days")
        if self.minimum_minutes_between_classes:
            out.append("minimum_minutes_between_classes")
        return out

    def without(self, name: str) -> SchedulePreferences:
        defaults = SchedulePreferences()
        return self.model_copy(update={name: getattr(defaults, name)})


#: Server-side bounds (CoursePilot settings, protecting the API).
MAX_REQUESTED_COURSES = 8
DEFAULT_MAX_RESULTS = 10
MAX_RESULTS_LIMIT = 25


# --------------------------------------------------------------------------
# evidence
# --------------------------------------------------------------------------


class MeetingInterval(BaseModel):
    """One normalized meeting row, with its raw SOC values kept."""

    kind: MeetingKind
    day: str | None = None
    start_minute: int | None = None          # minutes after midnight
    end_minute: int | None = None
    start_date: date | None = None           # from the section's sessionDates, if published
    end_date: date | None = None
    mode_code: str | None = None
    mode_desc: str | None = None
    campus: str | None = None                # SOC campusAbbrev (CAC, BUS, LIV, ONL, ...)
    campus_name: str | None = None
    building: str | None = None
    room: str | None = None
    raw: dict = Field(default_factory=dict)  # meetingDay / start / end as stored


class RestrictionEntry(BaseModel):
    kind: str                                # major | unit | minor | unit_major | honor_program
    code: str
    unit_code: str | None = None


class RestrictionEvidence(BaseModel):
    outcome: RestrictionOutcome
    entries: list[RestrictionEntry] = Field(default_factory=list)
    matched: RestrictionEntry | None = None
    open_to_text: str | None = None
    #: Section prose CoursePilot does not interpret ("JUNIORS AND SENIORS").
    eligibility_text: str | None = None
    #: SOC special-permission requirement for adding the section.
    special_permission: str | None = None
    reasons: list[str] = Field(default_factory=list)


class AvailabilityEvidence(BaseModel):
    state: AvailabilityState
    freshness: AvailabilityFreshness
    observed_at: str | None = None           # ISO timestamp of the SOC download
    source: str = "rutgers_soc_courses_json"
    source_hash: str | None = None


class EquivalentSection(BaseModel):
    """Another index of the same course with IDENTICAL meetings, dates,
    campus, restriction outcome and time verification - interchangeable for
    every structural rule (it may differ in instructor or availability)."""

    index_number: str
    section_number: str
    availability: AvailabilityEvidence
    instructors: list[str] = Field(default_factory=list)


class SectionChoice(BaseModel):
    """One registration index chosen for one requested course."""

    course: str                              # the REQUESTED course code
    component: str = "primary"               # primary | required_companion
    course_string: str
    supplement_code: str = ""
    title: str | None = None
    section_number: str
    index_number: str                        # what a student types into WebReg
    campus_code: str
    meetings: list[MeetingInterval]
    instructors: list[str] = Field(default_factory=list)
    availability: AvailabilityEvidence
    restriction: RestrictionEvidence
    cross_listed_indexes: list[str] = Field(default_factory=list)
    time_verified: bool                      # every meeting is timed or asynchronous
    notes: str | None = None
    #: Structurally identical alternatives; the choice is the lowest index.
    equivalent_sections: list[EquivalentSection] = Field(default_factory=list)


class ScheduleIssue(BaseModel):
    severity: Severity
    code: str
    message: str
    courses: list[str] = Field(default_factory=list)
    indexes: list[str] = Field(default_factory=list)
    details: dict = Field(default_factory=dict)


class ScheduleScore(BaseModel):
    """The lexicographic objective, component by component (smaller first)."""

    needs_confirmation: int
    unknown_time_sections: int
    preference_misses: int
    class_days: int
    gap_minutes: int
    closed_sections_if_live: int


class ScheduleOption(BaseModel):
    rank: int
    choices: list[SectionChoice]
    score: ScheduleScore
    reasons: list[Reason] = Field(default_factory=list)
    issues: list[ScheduleIssue] = Field(default_factory=list)
    days: list[str] = Field(default_factory=list)
    closed_sections_in_snapshot: list[str] = Field(default_factory=list)


class SearchStats(BaseModel):
    nodes: int = 0
    pruned: int = 0                          # branches cut by forward checking
    bound_pruned: int = 0                    # branches cut by the ranking bound
    solutions_considered: int = 0            # complete schedules reached
    node_limit: int
    solution_limit: int
    limit_reached: bool = False
    candidates_per_course: dict[str, int] = Field(default_factory=dict)
    #: After grouping structurally identical sections.
    distinct_patterns_per_course: dict[str, int] = Field(default_factory=dict)
    cartesian_product: int = 0
    pattern_product: int = 0


class ScheduleMetadata(BaseModel):
    schedule_engine_version: str = SCHEDULE_ENGINE_VERSION
    term_code: str
    settings_source: str = "coursepilot_schedule_setting_not_rutgers_policy"
    section_dataset: list[str] = Field(default_factory=list)   # SOC term:hash
    availability_freshness: AvailabilityFreshness | None = None
    availability_observed_at: str | None = None
    ranking_objective: list[str] = Field(default_factory=list)
    search: SearchStats


class ScheduleResult(BaseModel):
    term_code: str
    status: ScheduleStatus
    requested_courses: list[str]
    preferences: SchedulePreferences
    options: list[ScheduleOption] = Field(default_factory=list)
    issues: list[ScheduleIssue] = Field(default_factory=list)
    metadata: ScheduleMetadata

    def canonical_json(self) -> str:
        return self.model_dump_json()


__all__ = [
    "DEFAULT_MAX_RESULTS", "MAX_REQUESTED_COURSES", "MAX_RESULTS_LIMIT",
    "SCHEDULE_ENGINE_VERSION", "TIME_UNKNOWN_KINDS", "WEEKDAYS", "AvailabilityEvidence",
    "AvailabilityFreshness", "AvailabilityState", "EquivalentSection", "MeetingInterval",
    "MeetingKind", "Reason",
    "RestrictionEntry", "RestrictionEvidence", "RestrictionOutcome", "ScheduleIssue",
    "ScheduleMetadata", "ScheduleOption", "ScheduleResult", "ScheduleScore",
    "ScheduleStatus", "SchedulePreferences", "SearchStats", "SectionChoice", "Severity",
]
