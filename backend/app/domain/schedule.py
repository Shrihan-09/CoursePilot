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

Phase 6.7 - three independent axes, never merged into one "valid" flag:

    eligibility           may the student take the COURSE   (Phase 6.4, service)
    structural validity   do the SECTIONS fit together      (this engine)
    availability          can they register NOW             (app.domain.availability)

Availability is evaluated per REGISTRATION BUNDLE: a course's primary index
plus every required companion index (01:750:193 + its LB lab). The bundle
is OPEN only if every component is OPEN, CLOSED if any is CLOSED, else
UNKNOWN. An ACADEMIC co-requisite (01:750:229 needs 01:750:227) is a second
course with its own bundle - never folded into the first.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.availability import AvailabilityStatus, Freshness
from app.domain.modality import CourseModalityOptions, ModalityEvidence

#: Bumped whenever the same inputs could produce different options.
SCHEDULE_ENGINE_VERSION = "6.8.0"

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


#: open | closed | unknown - see app.domain.availability.
AvailabilityState = AvailabilityStatus
#: current (an accepted availability refresh within the current window of the
#: request) | stale (an older observation, shown with its time, never as
#: "now") | unobserved. Phase 6.6's "archived_snapshot" is "stale".
AvailabilityFreshness = Freshness


class AvailabilityMode(StrEnum):
    """How availability affects the options (a student setting).

    prefer_open   (default) never excludes a section: with CURRENT availability,
                  options whose sections are open rank first; closed and
                  unknown ones are still shown, labelled.
    require_open  only sections OPEN as of a CURRENT observation are scheduled;
                  without current availability nothing can be scheduled, and
                  the result says so (never a silent fallback to old data).
    """

    PREFER_OPEN = "prefer_open"
    REQUIRE_OPEN = "require_open"


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
    ALL_SECTIONS_OPEN_CURRENT = "all_sections_open_current"
    ALL_SECTIONS_OPEN_WHEN_LAST_OBSERVED = "all_sections_open_when_last_observed"
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
    AVAILABILITY (Phase 6.7): `availability` - see AvailabilityMode; a hard
      constraint only as require_open.
    """

    model_config = ConfigDict(extra="forbid")

    earliest_start: str | None = Field(default=None, pattern=_HHMM)
    latest_end: str | None = Field(default=None, pattern=_HHMM)
    avoid_days: list[str] = Field(default_factory=list, max_length=7)
    minimum_minutes_between_classes: int = Field(default=0, ge=0, le=120)
    preferred_campuses: list[str] = Field(default_factory=list, max_length=8)
    availability: AvailabilityMode = AvailabilityMode.PREFER_OPEN

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
        out = self.time_constraints()
        if self.availability is AvailabilityMode.REQUIRE_OPEN:
            out.append("availability")
        return out

    def time_constraints(self) -> list[str]:
        """The hard constraints about meeting TIMES (checkable only on timed meetings)."""
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
    """One registration index's availability. Render as
    "<state>, current as of <observed_at>" only when freshness is current."""

    state: AvailabilityState
    freshness: AvailabilityFreshness
    observed_at: str | None = None           # ISO time CoursePilot observed it (Rutgers gives none)
    source: str | None = None                # openSections.json | courses.json
    source_hash: str | None = None
    since: str | None = None                 # first observation of the current state
    #: e.g. "sources_disagree:courses.json=closed,openSections.json=open",
    #: "structural_snapshot_not_an_availability_refresh", "not_observed".
    reasons: list[str] = Field(default_factory=list)


class ComponentAvailability(BaseModel):
    """One registration index of a bundle."""

    component: str                           # primary | required_companion
    supplement_code: str = ""
    index_number: str
    state: AvailabilityState
    freshness: AvailabilityFreshness
    observed_at: str | None = None


class CourseAvailability(BaseModel):
    """A requested course's REGISTRATION BUNDLE (primary + required companions).

    state: open only if every component is open; closed if any is closed;
    otherwise unknown. `reasons` name the component responsible, e.g.
    "required_companion_closed:13384" - no prose parsing needed.
    """

    course: str
    state: AvailabilityState
    freshness: AvailabilityFreshness         # the least fresh component
    as_of: str | None = None                 # the OLDEST component observation
    components: list[ComponentAvailability]
    modality: ModalityEvidence = Field(default_factory=ModalityEvidence)
    reasons: list[str] = Field(default_factory=list)


class OptionAvailability(BaseModel):
    """The option as a whole: every requested course's bundle (an academic
    co-requisite is a separate course, so it is a separate bundle here)."""

    state: AvailabilityState
    freshness: AvailabilityFreshness
    as_of: str | None = None
    #: state is open AND freshness is current - "all of these can be registered now".
    all_open_now: bool = False
    courses: list[CourseAvailability] = Field(default_factory=list)


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
    #: primary | required_companion (a second registration record of the SAME
    #: course, e.g. 01:750:193 "LB" - see component_evidence)
    component: str = "primary"
    course_string: str
    supplement_code: str = ""
    title: str | None = None
    section_number: str
    index_number: str                        # what a student types into WebReg
    campus_code: str
    meetings: list[MeetingInterval]
    modality: ModalityEvidence = Field(default_factory=ModalityEvidence)
    instructors: list[str] = Field(default_factory=list)
    availability: AvailabilityEvidence
    restriction: RestrictionEvidence
    cross_listed_indexes: list[str] = Field(default_factory=list)
    time_verified: bool                      # every meeting is timed or asynchronous
    notes: str | None = None
    #: Structurally identical alternatives; the choice is the lowest index.
    equivalent_sections: list[EquivalentSection] = Field(default_factory=list)
    #: Phase 6.6.1 - for clients, no note parsing needed:
    credits: Decimal | None = None           # this record's credits (0 for an LB lab)
    #: Meeting kinds under THIS one index, e.g. ["LEC", "RECIT"] - one
    #: registration with several meetings, not several registrations.
    meeting_components: list[str] = Field(default_factory=list)
    #: Why a required_companion was included: the Rutgers note, verbatim.
    component_evidence: str | None = None
    #: Meeting times Rutgers describes only in the section note (not in the
    #: structured meetings) - conflict freedom cannot be verified.
    meeting_text_in_notes: str | None = None


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
    #: Chosen sections CLOSED / UNKNOWN as of a CURRENT observation. Stale
    #: availability never reorders options (it is reported, not ranked).
    closed_sections_if_current: int = 0
    unknown_availability_if_current: int = 0


class ScheduleOption(BaseModel):
    rank: int
    choices: list[SectionChoice]
    #: Sum of the chosen records' credits; None if any is variable/unknown.
    total_credits: Decimal | None = None
    score: ScheduleScore
    reasons: list[Reason] = Field(default_factory=list)
    issues: list[ScheduleIssue] = Field(default_factory=list)
    days: list[str] = Field(default_factory=list)
    availability: OptionAvailability


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
    #: Term-level: the latest availability evidence for the term.
    availability_freshness: AvailabilityFreshness | None = None
    availability_observed_at: str | None = None
    availability_source: str | None = None
    availability_refresh_id: str | None = None
    availability_mode: AvailabilityMode = AvailabilityMode.PREFER_OPEN
    availability_current_window_seconds: int | None = None
    ranking_objective: list[str] = Field(default_factory=list)
    search: SearchStats


class CourseRelationship(BaseModel):
    """A relationship that shaped the request, for clients and explanations.

    kind:
      registration_component     - `related` (same course, another record) must be
                                   registered too; verified from `evidence`
      registration_component_unverified - a second record exists, Rutgers does
                                   not say it is required; NOT scheduled
      academic_corequisite       - Phase 6.4: `course` requires `related` in the
                                   same term; satisfied by this request
    """

    kind: str
    course: str
    related: str
    evidence: str | None = None
    source: str | None = None                # where the evidence was published


class ScheduleResult(BaseModel):
    modality_options: list[CourseModalityOptions] = Field(default_factory=list)
    term_code: str
    status: ScheduleStatus
    requested_courses: list[str]
    preferences: SchedulePreferences
    options: list[ScheduleOption] = Field(default_factory=list)
    issues: list[ScheduleIssue] = Field(default_factory=list)
    relationships: list[CourseRelationship] = Field(default_factory=list)
    metadata: ScheduleMetadata

    def canonical_json(self) -> str:
        return self.model_dump_json()


__all__ = [
    "DEFAULT_MAX_RESULTS", "MAX_REQUESTED_COURSES", "MAX_RESULTS_LIMIT",
    "SCHEDULE_ENGINE_VERSION", "TIME_UNKNOWN_KINDS", "WEEKDAYS", "AvailabilityEvidence",
    "AvailabilityFreshness", "AvailabilityMode", "AvailabilityState", "ComponentAvailability",
    "CourseAvailability", "CourseRelationship", "EquivalentSection", "OptionAvailability",
    "MeetingInterval",
    "MeetingKind", "Reason",
    "RestrictionEntry", "RestrictionEvidence", "RestrictionOutcome", "ScheduleIssue",
    "ScheduleMetadata", "ScheduleOption", "ScheduleResult", "ScheduleScore",
    "ScheduleStatus", "SchedulePreferences", "SearchStats", "SectionChoice", "Severity",
]
