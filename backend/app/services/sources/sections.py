"""The section data view (Phase 7.3): one read-only place for section facts, each with provenance.

With CSP not adopted (PHASE_7_3_PLAN.md §1a) the "combined data view" is thin:

* STRUCTURAL facts (identity, meetings, instructors, restrictions) come from
  SOC `courses.json` - the `data_source` row the section was last stamped by.
* AVAILABILITY comes from services.availability.lookup, unchanged: the Phase
  6.7 rule decides (two sources that disagree at the same moment -> UNKNOWN).
* RESTRICTION NAMES - the one CSP-only convenience - are filled from PUBLIC SOC
  data only: a major/minor code that is a SOC subject code takes that
  subject's description; a unit code takes the SOC school description. Where
  no public name exists the code is shown alone; nothing is guessed.

* CORE CODES (Phase 7.6) come from the same term's SOC `courses.json`
  (`offering_core_code`): the codes a course carries FOR THAT TERM, verbatim.
  Which school's Core a code belongs to is decided by that school's Core set,
  never here.

Every value carries {source, data_source_id, fetched_at}. Engines keep their
own semantics; this view only reads.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Course,
    CourseOffering,
    CourseSection,
    DataSource,
    SectionInstructor,
    SectionMeeting,
    SectionRestriction,
    Subject,
)

COURSES_JSON = "soc_courses_json"


class Provenance(BaseModel):
    source: str
    data_source_id: str | None = None
    fetched_at: datetime | None = None


class Sourced(BaseModel):
    value: Any
    provenance: Provenance


class RestrictionName(BaseModel):
    kind: str                       # major | minor | unit | unit_major | honor_program
    code: str
    unit_code: str | None = None
    name: str | None = None         # None: no public name - show the code
    name_source: str | None = None  # soc_subject_description | soc_school_description


class SectionFacts(BaseModel):
    term_code: str
    index_number: str
    course_string: Sourced
    section_number: Sourced
    title: Sourced
    meetings: Sourced
    instructors: Sourced
    restrictions: Sourced
    availability: Sourced


def restriction_names(session: Session, rows: list[SectionRestriction]) -> list[RestrictionName]:
    subjects = {code: desc for code, desc in session.execute(
        select(Subject.code, Subject.description)) if desc}
    schools = {code: desc for code, desc in session.execute(
        select(Course.school_code, Course.school_description).distinct()) if code and desc}
    out = []
    for r in sorted(rows, key=lambda r: r.ordinal):
        name = source = None
        if r.kind in ("major", "minor", "unit_major") and r.code in subjects:
            name, source = subjects[r.code], "soc_subject_description"
        elif r.kind == "unit" and r.code in schools:
            name, source = schools[r.code], "soc_school_description"
        out.append(RestrictionName(kind=r.kind, code=r.code, unit_code=r.unit_code,
                                   name=name, name_source=source))
    return out


def section_facts(session: Session, term_code: str, indexes: list[str], *,
                  reference_time: datetime) -> dict[str, SectionFacts]:
    from app.services.availability import SectionRef, lookup

    rows = session.execute(
        select(CourseSection, Course, DataSource)
        .join(CourseOffering, CourseOffering.id == CourseSection.offering_id)
        .join(Course, Course.id == CourseOffering.course_id)
        .join(DataSource, DataSource.id == CourseSection.source_id)
        .where(CourseSection.term_code == term_code, CourseSection.index_number.in_(indexes))
    ).all()
    ids = [s.id for s, _, _ in rows]
    meetings: dict[uuid.UUID, list] = defaultdict(list)
    instructors: dict[uuid.UUID, list] = defaultdict(list)
    restrictions: dict[uuid.UUID, list] = defaultdict(list)
    if ids:
        for m in session.scalars(select(SectionMeeting).where(SectionMeeting.section_id.in_(ids))
                                 .order_by(SectionMeeting.meeting_index)):
            meetings[m.section_id].append({
                "day": m.meeting_day, "start": m.start_time_military, "end": m.end_time_military,
                "mode": m.meeting_mode_desc, "campus": m.campus_abbrev,
                "building": m.building_code, "room": m.room_number})
        for i in session.scalars(select(SectionInstructor)
                                 .where(SectionInstructor.section_id.in_(ids))
                                 .order_by(SectionInstructor.instructor_index)):
            instructors[i.section_id].append(i.name)
        for r in session.scalars(select(SectionRestriction)
                                 .where(SectionRestriction.section_id.in_(ids))):
            restrictions[r.section_id].append(r)
    refs = [SectionRef(s.id, s.index_number, bool(s.open_status), s.source_id)
            for s, _, _ in rows]
    availability, provenance = lookup(session, term_code, refs, reference_time,
                                      downloads={d.id: d.retrieved_at for _, _, d in rows})
    out = {}
    for section, course, source in rows:
        soc = Provenance(source=COURSES_JSON, data_source_id=str(source.id),
                         fetched_at=source.retrieved_at)
        resolved = availability.get(section.index_number)
        live = Provenance(source=(resolved.source if resolved and resolved.source
                                  else "unobserved"),
                          data_source_id=provenance.get("refresh_id"),
                          fetched_at=resolved.observed_at if resolved else None)
        out[section.index_number] = SectionFacts(
            term_code=term_code, index_number=section.index_number,
            course_string=Sourced(value=course.course_string, provenance=soc),
            section_number=Sourced(value=section.section_number, provenance=soc),
            title=Sourced(value=course.title, provenance=soc),
            meetings=Sourced(value=meetings[section.id], provenance=soc),
            instructors=Sourced(value=instructors[section.id], provenance=soc),
            restrictions=Sourced(value=[n.model_dump() for n in restriction_names(
                session, restrictions[section.id])], provenance=soc),
            availability=Sourced(value=_availability(resolved), provenance=live))
    return out


class TermCoreCodes(BaseModel):
    """The Core codes SOC lists for one course in one term (Phase 7.6)."""

    course_string: str
    term_code: str
    codes: list[str]
    provenance: Provenance


def core_codes(session: Session, term_code: str,
               course_strings: list[str] | set[str]) -> dict[str, TermCoreCodes]:
    """course -> the Core codes SOC published for it in `term_code`.

    Only courses OFFERED in that term appear: a course with an offering and no
    codes maps to an empty list (SOC lists none), a course with no offering in
    the term is absent (nothing was published to read)."""
    from app.models.core_codes import OfferingCoreCode

    keys = sorted(set(course_strings))
    if not keys:
        return {}
    out: dict[str, TermCoreCodes] = {}
    for key, source in session.execute(
            select(Course.course_string, DataSource)
            .join(CourseOffering, CourseOffering.course_id == Course.id)
            .join(DataSource, DataSource.id == CourseOffering.source_id)
            .where(CourseOffering.term_code == term_code, Course.course_string.in_(keys),
                   Course.supplement_code == "")):
        out.setdefault(key, TermCoreCodes(
            course_string=key, term_code=term_code, codes=[],
            provenance=Provenance(source=COURSES_JSON, data_source_id=str(source.id),
                                  fetched_at=source.retrieved_at)))
    for key, code in session.execute(
            select(Course.course_string, OfferingCoreCode.code)
            .join(OfferingCoreCode, OfferingCoreCode.course_id == Course.id)
            .where(OfferingCoreCode.term_code == term_code, Course.course_string.in_(keys),
                   Course.supplement_code == "")
            .order_by(OfferingCoreCode.code)):
        if key in out and code not in out[key].codes:
            out[key].codes.append(code)
    return out


def _availability(resolved) -> dict:
    """The Phase 6.7 verdict as decided by services.availability - never re-judged."""
    if resolved is None:
        return {"status": "unknown", "freshness": None, "reasons": ["not_observed"]}
    return {"status": str(resolved.status.value), "freshness": str(resolved.freshness.value),
            "since": resolved.since, "reasons": list(resolved.reasons)}


__all__ = ["Provenance", "RestrictionName", "SectionFacts", "Sourced", "TermCoreCodes",
           "core_codes", "restriction_names", "section_facts"]
