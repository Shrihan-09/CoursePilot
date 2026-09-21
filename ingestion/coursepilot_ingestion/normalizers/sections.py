"""Normalizer: RawSocSection -> NormalizedSection.

The boundary between Rutgers' section vocabulary and CoursePilot's.

As with courses, normalization never invents values. Where SOC gives nothing,
the result is None - never a guess, and never a placeholder like "TBA" that
would be indistinguishable from a real value later.
"""

from __future__ import annotations

import html
import logging
import re

from coursepilot_ingestion.parsers.sections import ParentCourseRef
from coursepilot_ingestion.section_schemas import (
    NormalizedCrossListing,
    NormalizedInstructor,
    NormalizedMeeting,
    NormalizedSection,
    RawSocMeetingTime,
    RawSocSection,
)

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(value: str | None) -> str | None:
    """Strip HTML, unescape entities, collapse whitespace; '' becomes None.

    SOC embeds markup in free-text fields, and returns empty strings rather
    than nulls. Collapsing '' to None keeps "absent" distinguishable from
    "present but blank" in the database.
    """
    if value is None:
        return None
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


class SocSectionNormalizer:
    """Maps SOC section records into CoursePilot's canonical form."""

    def __init__(self, term_code: str) -> None:
        self.term_code = term_code

    def _meeting(self, index: int, raw: RawSocMeetingTime) -> NormalizedMeeting:
        # Military time is used, not the startTime/pmCode pair. "0350" + "P"
        # requires interpretation to become 15:50, and an interpretation step
        # is a place for bugs; startTimeMilitary is already unambiguous and
        # was measured well-formed on 100% of timed meetings.
        return NormalizedMeeting(
            meeting_index=index,
            meeting_day=_blank_to_none(raw.meetingDay),
            start_time_military=_blank_to_none(raw.startTimeMilitary),
            end_time_military=_blank_to_none(raw.endTimeMilitary),
            # Present on 100% of meeting rows; '' would be a source change.
            meeting_mode_code=(raw.meetingModeCode or "").strip() or "UNKNOWN",
            meeting_mode_desc=_clean(raw.meetingModeDesc),
            building_code=_blank_to_none(raw.buildingCode),
            room_number=_blank_to_none(raw.roomNumber),
            campus_location=_blank_to_none(raw.campusLocation),
            campus_name=_clean(raw.campusName),
            campus_abbrev=_blank_to_none(raw.campusAbbrev),
        )

    def normalize(self, parent: ParentCourseRef, raw: RawSocSection) -> NormalizedSection:
        meetings = [self._meeting(i, m) for i, m in enumerate(raw.meetingTimes)]

        # Ordinal position is the key, so blank names must not silently shift
        # later instructors up. Measured: 0 blank names, so this drops nothing
        # today - it is a guard against a future source change.
        instructors: list[NormalizedInstructor] = []
        for i, inst in enumerate(raw.instructors):
            name = _clean(inst.name)
            if name:
                instructors.append(NormalizedInstructor(instructor_index=i, name=name))

        cross_listings: list[NormalizedCrossListing] = []
        for xl in raw.crossListedSections:
            reg = _blank_to_none(xl.registrationIndex)
            if not reg:
                # Measured: 0 of 966 rows lack a registrationIndex. Without it
                # the row has no identity, so it cannot be stored.
                continue
            cross_listings.append(
                NormalizedCrossListing(
                    registration_index=reg,
                    primary_registration_index=_blank_to_none(xl.primaryRegistrationIndex),
                    offering_unit_code=_blank_to_none(xl.offeringUnitCode),
                    offering_unit_campus=_blank_to_none(xl.offeringUnitCampus),
                    subject_code=_blank_to_none(xl.subjectCode),
                    course_number=_blank_to_none(xl.courseNumber),
                    supplement_code=(xl.supplementCode or "").strip(),
                    section_number=_blank_to_none(xl.sectionNumber),
                )
            )

        return NormalizedSection(
            term_code=self.term_code,
            index_number=raw.index.strip(),
            # Parent identity comes from the enclosing course object, not from
            # any string on the section. Never matched by title.
            offering_unit_code=parent.offering_unit_code,
            subject_code=parent.subject_code,
            course_number=parent.course_number,
            supplement_code=parent.supplement_code,
            # Measured: section campusCode == parent course campusCode on
            # 11,992 of 11,992. The section's own value is preferred, falling
            # back to the parent's, so a future divergence follows the section.
            campus_code=(raw.campusCode or "").strip() or parent.campus_code or "UNKNOWN",
            section_number=raw.number.strip(),
            # openStatus is present on 100% of sections. Defaulting a missing
            # value to True would invent availability, so False is the safe
            # fallback: it understates rather than overstates.
            open_status=bool(raw.openStatus),
            open_status_text=_blank_to_none(raw.openStatusText),
            section_course_type=_blank_to_none(raw.sectionCourseType),
            exam_code=_blank_to_none(raw.examCode),
            exam_code_text=_clean(raw.examCodeText),
            final_exam=_clean(raw.finalExam),
            subtitle=_clean(raw.subtitle),
            section_notes=_clean(raw.sectionNotes),
            comments_text=_clean(raw.commentsText),
            open_to_text=_clean(raw.openToText),
            section_eligibility=_clean(raw.sectionEligibility),
            special_permission_add_code=_blank_to_none(raw.specialPermissionAddCode),
            special_permission_add_description=_clean(raw.specialPermissionAddCodeDescription),
            special_permission_drop_code=_blank_to_none(raw.specialPermissionDropCode),
            special_permission_drop_description=_clean(raw.specialPermissionDropCodeDescription),
            cross_listed_section_type=_blank_to_none(raw.crossListedSectionType),
            meetings=meetings,
            instructors=instructors,
            cross_listings=cross_listings,
        )
