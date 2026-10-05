"""Section candidates for one term (Phase 6.6): loaded in batches, never per section.

## What a requested course becomes

A Rutgers registration index already bundles a section's meetings: 1,024 Fall
2026 sections carry LEC and RECIT rows under ONE index (01:750:203 section 01
= T/F lecture + M recitation). Choosing one index chooses all its meetings.

The one structural exception found: Physics 01:750:193 and 194 publish a
SECOND course record with the same course string - supplement "LB", 0
credits, "PHYSICS FOR SCI LAB" - whose sections say "MUST REGISTER FOR BOTH
A REC & A LAB TOGETHER". Such a record is a REQUIRED COMPANION: the student
needs one index from each record, paired freely (SOC links no particular
recitation to a particular lab). A companion is recognized only when BOTH
are true:

  * structural - same course string, a non-empty supplement code, 0 credits;
  * explicit   - a section or course note of that record says registration
    in both is required.

A supplement record meeting only the first test is reported
(LINKED_COMPONENT_UNVERIFIED) and never silently bundled or dropped.

## Section restrictions

SOC's structured "open to" list (section_restriction) is satisfied when the
student's KNOWN record matches any entry: their declared major code, their
school's unit code, or that unit/major pair. CoursePilot records one
declared program and no minors, second majors, honors membership or class
standing, so a section whose entries do not match is UNKNOWN, not failed -
the student may hold a minor CoursePilot has never seen. NOT_SATISFIED is
reached only for attributes declared complete (`StudentAttributes.complete`),
which no production record is today. Special-permission sections and
sections with uninterpreted eligibility prose ("JUNIORS AND SENIORS") are
UNKNOWN as well. UNKNOWN is never "eligible": it is scheduled only as a
needs-confirmation option, ranked after verified ones.

## Availability

`open_status` comes from a SOC courses.json DOWNLOAD - an archived snapshot
with a timestamp (data_source.retrieved_at), never a live signal. Every
choice carries that provenance; nothing here says "open right now".
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.schedule import (
    AvailabilityEvidence,
    AvailabilityFreshness,
    AvailabilityState,
    MeetingInterval,
    RestrictionEntry,
    RestrictionEvidence,
    RestrictionOutcome,
)
from app.models import (
    Course,
    CourseOffering,
    CourseSection,
    DataSource,
    Program,
    ProgramVersion,
    School,
    SectionCrossListing,
    SectionInstructor,
    SectionMeeting,
    SectionRestriction,
    Student,
)
from app.services.scheduling.meetings import normalize, time_verified

#: CoursePilot school code -> Rutgers offering-unit code, as SOC itself pairs
#: them in openToText ("UNIT/MAJOR: 01/202 (School of Arts and Sciences / ...").
SCHOOL_UNIT_CODES = {"SAS": "01"}

_BOTH_REQUIRED = re.compile(r"MUST REGISTER FOR BOTH|REGISTER FOR BOTH|BOTH A REC|TOGETHER",
                            re.I)
SOC_KIND = "rutgers_official_api"


@dataclass(frozen=True)
class StudentAttributes:
    major_codes: frozenset[str] = frozenset()
    unit_codes: frozenset[str] = frozenset()
    #: True only when the sets above are known to be the student's COMPLETE
    #: majors/units (then a non-matching restriction is NOT_SATISFIED).
    complete: bool = False


def student_attributes(session: Session, student: Student) -> StudentAttributes:
    row = session.execute(
        select(Program.code, School.code)
        .join(ProgramVersion, ProgramVersion.program_id == Program.id)
        .join(School, School.id == Program.school_id)
        .where(ProgramVersion.id == student.program_version_id)).first()
    if row is None:
        return StudentAttributes()
    major, school = row
    unit = SCHOOL_UNIT_CODES.get(school)
    return StudentAttributes(major_codes=frozenset({major}),
                             unit_codes=frozenset({unit}) if unit else frozenset())


def evaluate_restriction(entries: list[RestrictionEntry], attrs: StudentAttributes, *,
                         open_to_text: str | None = None, eligibility_text: str | None = None,
                         special_permission: str | None = None) -> RestrictionEvidence:
    reasons: list[str] = []
    matched = None
    for e in entries:
        if ((e.kind == "major" and e.code in attrs.major_codes)
                or (e.kind == "unit" and e.code in attrs.unit_codes)
                or (e.kind == "unit_major" and e.code in attrs.major_codes
                    and e.unit_code in attrs.unit_codes)):
            matched = e
            break
    if not entries:
        outcome = RestrictionOutcome.UNRESTRICTED
    elif matched is not None:
        outcome = RestrictionOutcome.SATISFIED
    elif attrs.complete and not any(e.kind in ("minor", "honor_program") for e in entries):
        outcome = RestrictionOutcome.NOT_SATISFIED
        reasons.append("no_listed_major_or_school_matches")
    else:
        outcome = RestrictionOutcome.UNKNOWN
        reasons.append("student_attributes_incomplete")
    # Gates CoursePilot cannot evaluate turn a clear section into UNKNOWN.
    if outcome in (RestrictionOutcome.UNRESTRICTED, RestrictionOutcome.SATISFIED):
        if special_permission:
            outcome = RestrictionOutcome.UNKNOWN
            reasons.append("special_permission_required")
        if eligibility_text:
            outcome = RestrictionOutcome.UNKNOWN
            reasons.append("eligibility_text_not_interpreted")
    return RestrictionEvidence(outcome=outcome, entries=entries, matched=matched,
                               open_to_text=open_to_text, eligibility_text=eligibility_text,
                               special_permission=special_permission, reasons=reasons)


@dataclass
class SectionCandidate:
    slot: str
    course: str                        # requested key
    component: str                     # primary | required_companion
    course_string: str
    supplement_code: str
    title: str | None
    section_id: uuid.UUID
    section_number: str
    index_number: str
    campus_code: str
    meetings: list[MeetingInterval]
    instructors: list[str]
    availability: AvailabilityEvidence
    restriction: RestrictionEvidence
    cross_listed: list[str]
    notes: str | None
    time_verified: bool


@dataclass
class TermData:
    term_code: str
    published: bool                    # any section data for the term at all
    coverage: str | None
    dataset: list[str]
    observed_at: str | None
    source_hash: str | None
    candidates: dict[str, list[SectionCandidate]] = field(default_factory=dict)
    slots_of: dict[str, list[str]] = field(default_factory=dict)       # course -> slots
    not_offered: list[str] = field(default_factory=list)
    unverified_components: dict[str, list[str]] = field(default_factory=dict)
    cross_listed_requests: list[tuple[str, str]] = field(default_factory=list)


def _note_text(*parts: str | None) -> str:
    return " ".join(p for p in parts if p)


def load_term(session: Session, term_code: str, courses: list[str],
              attrs: StudentAttributes) -> TermData:
    """Everything the search needs, in a fixed number of queries."""
    sources = session.execute(
        select(DataSource.content_hash, DataSource.coverage, DataSource.retrieved_at)
        .where(DataSource.kind == SOC_KIND, DataSource.term_code == term_code)
        .order_by(DataSource.retrieved_at, DataSource.content_hash)).all()
    published = session.scalar(
        select(CourseSection.id).where(CourseSection.term_code == term_code).limit(1)) is not None
    latest = sources[-1] if sources else None
    data = TermData(
        term_code=term_code, published=published,
        coverage=("complete" if any(s.coverage == "complete" for s in sources)
                  else (latest.coverage if latest else None)),
        dataset=[f"{term_code}:{s.content_hash[:16]}" for s in sources],
        observed_at=latest.retrieved_at.isoformat() if latest else None,
        source_hash=latest.content_hash[:16] if latest else None)
    if not published:
        return data

    rows = session.execute(
        select(Course, CourseSection)
        .join(CourseOffering, CourseOffering.course_id == Course.id)
        .join(CourseSection, CourseSection.offering_id == CourseOffering.id)
        .where(Course.course_string.in_(sorted(set(courses))),
               CourseSection.term_code == term_code,
               CourseOffering.term_code == term_code)).all()
    ids = [s.id for _, s in rows]
    meetings, instructors, xlists, restrictions = (defaultdict(list) for _ in range(4))
    if ids:
        for m in session.scalars(select(SectionMeeting).where(SectionMeeting.section_id.in_(ids))
                                 .order_by(SectionMeeting.section_id,
                                           SectionMeeting.meeting_index)):
            meetings[m.section_id].append(m)
        for i in session.scalars(select(SectionInstructor)
                                 .where(SectionInstructor.section_id.in_(ids))
                                 .order_by(SectionInstructor.section_id,
                                           SectionInstructor.instructor_index)):
            instructors[i.section_id].append(i.name)
        for x in session.scalars(select(SectionCrossListing)
                                 .where(SectionCrossListing.section_id.in_(ids))):
            xlists[x.section_id].append(x.registration_index)
        for r in session.scalars(select(SectionRestriction)
                                 .where(SectionRestriction.section_id.in_(ids))
                                 .order_by(SectionRestriction.section_id,
                                           SectionRestriction.ordinal)):
            restrictions[r.section_id].append(
                RestrictionEntry(kind=r.kind, code=r.code, unit_code=r.unit_code))

    # Group by course record (course string + supplement).
    records: dict[tuple[str, str], list[tuple[Course, CourseSection]]] = defaultdict(list)
    for course, section in rows:
        records[(course.course_string, course.supplement_code)].append((course, section))

    availability_base = dict(freshness=AvailabilityFreshness.ARCHIVED,
                             observed_at=data.observed_at, source_hash=data.source_hash)
    for key in sorted(set(courses)):
        variants = sorted(sup for (cs, sup) in records if cs == key)
        if not variants:
            data.not_offered.append(key)
            continue
        primary = "" if "" in variants else variants[0]
        slots = [(primary, "primary")]
        for sup in variants:
            if sup == primary:
                continue
            course0 = records[(key, sup)][0][0]
            notes = _note_text(*(s.section_notes for _, s in records[(key, sup)]),
                               *(s.comments_text for _, s in records[(key, sup)]))
            if (course0.credits is not None and course0.credits == 0
                    and _BOTH_REQUIRED.search(notes)):
                slots.append((sup, "required_companion"))
            else:
                data.unverified_components.setdefault(key, []).append(sup or "(none)")
        data.slots_of[key] = []
        for sup, component in slots:
            slot = key if component == "primary" else f"{key}#{sup}"
            data.slots_of[key].append(slot)
            out = []
            for course, s in records[(key, sup)]:
                ms = [normalize(day=m.meeting_day, start=m.start_time_military,
                                end=m.end_time_military, mode_code=m.meeting_mode_code,
                                mode_desc=m.meeting_mode_desc, start_date=s.session_start_date,
                                end_date=s.session_end_date, campus=m.campus_abbrev,
                                campus_name=m.campus_name, building=m.building_code,
                                room=m.room_number) for m in meetings[s.id]]
                out.append(SectionCandidate(
                    slot=slot, course=key, component=component, course_string=key,
                    supplement_code=sup, title=course.title, section_id=s.id,
                    section_number=s.section_number, index_number=s.index_number,
                    campus_code=s.campus_code, meetings=ms, instructors=instructors[s.id],
                    availability=AvailabilityEvidence(
                        state=AvailabilityState.OPEN if s.open_status else AvailabilityState.CLOSED,
                        **availability_base),
                    restriction=evaluate_restriction(
                        restrictions[s.id], attrs, open_to_text=s.open_to_text,
                        eligibility_text=s.section_eligibility,
                        special_permission=s.special_permission_add_description
                        or s.special_permission_add_code),
                    cross_listed=sorted(xlists[s.id]), notes=s.section_notes,
                    time_verified=time_verified(ms)))
            data.candidates[slot] = sorted(out, key=lambda c: c.index_number)

    # Requested courses that are cross-listings of each other.
    by_index = {c.index_number: c.course for cands in data.candidates.values() for c in cands}
    pairs = set()
    for cands in data.candidates.values():
        for c in cands:
            for x in c.cross_listed:
                other = by_index.get(x)
                if other and other != c.course:
                    pairs.add(tuple(sorted((c.course, other))))
    data.cross_listed_requests = sorted(pairs)
    return data


__all__ = ["SCHOOL_UNIT_CODES", "SectionCandidate", "StudentAttributes", "TermData",
           "evaluate_restriction", "load_term", "student_attributes"]
