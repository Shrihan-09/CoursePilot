"""Pydantic schemas for SOC section ingestion.

Follows the same two-model pattern established for courses in `schemas.py`:

  * `RawSocSection` / `RawSocMeetingTime` - permissive mirrors of what SOC
    actually sends. We do not control that shape.
  * `NormalizedSection` / `NormalizedMeeting` / ... - CoursePilot's strict
    vocabulary. Everything downstream depends on these.

Kept in a separate module from `schemas.py` rather than appended to it, so the
course pipeline stays readable and section work cannot destabilize it.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Measured: 5 digits on 11,992 of 11,992 sections, numeric in every case.
INDEX_RE = re.compile(r"^\d{4,6}$")
# Measured: 4 digits, e.g. "1550". 82 distinct values, 0 malformed.
TIME_RE = re.compile(r"^\d{4}$")

# Measured meetingDay values. H = Thursday, U = Sunday.
VALID_MEETING_DAYS = frozenset({"M", "T", "W", "H", "F", "S", "U"})


# ---------------------------------------------------------------- raw models


class RawSocMeetingTime(BaseModel):
    model_config = ConfigDict(extra="allow")

    meetingDay: str | None = None
    startTimeMilitary: str | None = None
    endTimeMilitary: str | None = None
    startTime: str | None = None
    endTime: str | None = None
    pmCode: str | None = None
    meetingModeCode: str | None = None
    meetingModeDesc: str | None = None
    buildingCode: str | None = None
    roomNumber: str | None = None
    campusLocation: str | None = None
    campusName: str | None = None
    campusAbbrev: str | None = None
    baClassHours: str | None = None


class RawSocInstructor(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None


class RawSocCrossListing(BaseModel):
    model_config = ConfigDict(extra="allow")

    registrationIndex: str | None = None
    primaryRegistrationIndex: str | None = None
    offeringUnitCode: str | None = None
    offeringUnitCampus: str | None = None
    subjectCode: str | None = None
    courseNumber: str | None = None
    supplementCode: str | None = None
    sectionNumber: str | None = None


class RawSocSection(BaseModel):
    """A section object exactly as SOC returns it."""

    model_config = ConfigDict(extra="allow")

    index: str
    number: str
    campusCode: str | None = None

    openStatus: bool | None = None
    openStatusText: str | None = None

    sectionCourseType: str | None = None
    examCode: str | None = None
    examCodeText: str | None = None
    finalExam: str | None = None

    subtitle: str | None = None
    sectionNotes: str | None = None
    commentsText: str | None = None
    openToText: str | None = None
    sectionEligibility: str | None = None

    specialPermissionAddCode: str | None = None
    specialPermissionAddCodeDescription: str | None = None
    specialPermissionDropCode: str | None = None
    specialPermissionDropCodeDescription: str | None = None

    crossListedSectionType: str | None = None

    meetingTimes: list[RawSocMeetingTime] = Field(default_factory=list)
    instructors: list[RawSocInstructor] = Field(default_factory=list)
    crossListedSections: list[RawSocCrossListing] = Field(default_factory=list)


# --------------------------------------------------------- normalized models


class NormalizedMeeting(BaseModel):
    """One meeting pattern in CoursePilot's vocabulary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    meeting_index: int = Field(ge=0)

    meeting_day: str | None = None
    start_time_military: str | None = None
    end_time_military: str | None = None

    meeting_mode_code: str = Field(min_length=1, max_length=8)
    meeting_mode_desc: str | None = None

    building_code: str | None = None
    room_number: str | None = None
    campus_location: str | None = None
    campus_name: str | None = None
    campus_abbrev: str | None = None

    @field_validator("meeting_day")
    @classmethod
    def _check_day(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in VALID_MEETING_DAYS:
            raise ValueError(
                f"meeting_day {v!r} not in observed Rutgers values {sorted(VALID_MEETING_DAYS)}"
            )
        return v

    @field_validator("start_time_military", "end_time_military")
    @classmethod
    def _check_time(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not TIME_RE.match(v):
            raise ValueError(f"time {v!r} is not 4-digit military time (e.g. '1550')")
        return v

    @model_validator(mode="after")
    def _times_paired(self) -> NormalizedMeeting:
        # Measured: 0 of 17,457 meetings have a start without an end, or a day
        # without a time. A half-populated time would mean the source shape
        # changed, so it is an error rather than something to paper over.
        if (self.start_time_military is None) != (self.end_time_military is None):
            raise ValueError(
                "start and end time must both be present or both absent; "
                f"got start={self.start_time_military!r} end={self.end_time_military!r}"
            )
        return self

    @property
    def is_tba(self) -> bool:
        return self.meeting_day is None and self.start_time_military is None

    @property
    def crosses_midnight_or_zero_length(self) -> bool:
        """True when end <= start.

        Three real Rutgers meetings do this, so it is a warning rather than a
        rejection. See the validator.
        """
        if self.start_time_military is None or self.end_time_military is None:
            return False
        return self.end_time_military <= self.start_time_military


class NormalizedInstructor(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    instructor_index: int = Field(ge=0)
    name: str = Field(min_length=1)


class NormalizedCrossListing(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    registration_index: str = Field(min_length=1, max_length=16)
    primary_registration_index: str | None = None
    offering_unit_code: str | None = None
    offering_unit_campus: str | None = None
    subject_code: str | None = None
    course_number: str | None = None
    supplement_code: str = ""
    section_number: str | None = None


class NormalizedSection(BaseModel):
    """CoursePilot's canonical section."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # --- natural key ---
    term_code: str = Field(min_length=1)
    index_number: str = Field(min_length=1, max_length=16)

    # --- parent course identity (used to resolve the offering) ---
    offering_unit_code: str = Field(min_length=1, max_length=8)
    subject_code: str = Field(min_length=1, max_length=8)
    course_number: str = Field(min_length=1, max_length=16)
    supplement_code: str = Field(default="", max_length=8)
    campus_code: str = Field(min_length=1, max_length=16)

    section_number: str = Field(min_length=1, max_length=16)

    open_status: bool
    open_status_text: str | None = None

    section_course_type: str | None = None
    exam_code: str | None = None
    exam_code_text: str | None = None
    final_exam: str | None = None

    subtitle: str | None = None
    section_notes: str | None = None
    comments_text: str | None = None
    open_to_text: str | None = None
    section_eligibility: str | None = None

    special_permission_add_code: str | None = None
    special_permission_add_description: str | None = None
    special_permission_drop_code: str | None = None
    special_permission_drop_description: str | None = None

    cross_listed_section_type: str | None = None

    meetings: list[NormalizedMeeting] = Field(default_factory=list)
    instructors: list[NormalizedInstructor] = Field(default_factory=list)
    cross_listings: list[NormalizedCrossListing] = Field(default_factory=list)

    @field_validator("index_number")
    @classmethod
    def _check_index(cls, v: str) -> str:
        if not INDEX_RE.match(v):
            raise ValueError(
                f"index_number {v!r} is not a numeric Rutgers registration index "
                "(5 digits in all observed data)"
            )
        return v

    @property
    def course_natural_key(self) -> tuple[str, str, str, str]:
        """The parent course's natural key.

        Matching is by identifier, never by title string. This is the same
        4-tuple the Course model is keyed on.
        """
        return (
            self.offering_unit_code,
            self.subject_code,
            self.course_number,
            self.supplement_code,
        )

    @property
    def offering_natural_key(self) -> tuple[str, str, str, str, str, str]:
        """Course natural key + term + campus, i.e. which offering owns this."""
        return (*self.course_natural_key, self.term_code, self.campus_code)

    @property
    def natural_key(self) -> tuple[str, str]:
        """(term_code, index_number). See the CourseSection model docstring."""
        return (self.term_code, self.index_number)


class SectionIngestionStats(BaseModel):
    """Outcome of one section pipeline run."""

    fetched: int = 0
    parsed: int = 0
    parse_failed: int = 0
    validated: int = 0
    validation_failed: int = 0

    sections_inserted: int = 0
    sections_updated: int = 0
    meetings_written: int = 0
    instructors_written: int = 0
    cross_listings_written: int = 0

    # Sections whose parent offering is not in the database. Never silently
    # dropped: counted here and listed in `unmatched_details`.
    unmatched_offering: int = 0
    unmatched_details: list[str] = Field(default_factory=list)

    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_content_hash: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def summary(self) -> str:
        return (
            f"parsed={self.parsed} parse_failed={self.parse_failed} "
            f"validated={self.validated} validation_failed={self.validation_failed} "
            f"sections(+{self.sections_inserted}/~{self.sections_updated}) "
            f"meetings={self.meetings_written} instructors={self.instructors_written} "
            f"unmatched_offering={self.unmatched_offering}"
        )
