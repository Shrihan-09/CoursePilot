"""Chosen sections and the "Ready for WebReg" panel (Prompt 11 Part B, B1b step 4, B1c).

CoursePilot never signs in to WebReg and never registers anyone: that would
need the student's NetID, password and Duo. It prepares the hand-off instead:
the index numbers of the sections the student chose, copied in one tap, the
steps to paste them into WebReg, and warnings - a closed section (consider a
watch), a special-permission section, two chosen sections that overlap.

A chosen section must exist in that term's published sections for that
course; it is stored with the section's natural key and a SET NULL link, so a
section Rutgers later removes is reported, never silently retargeted.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Course, Student
from app.models.academic import CourseOffering
from app.models.assistant import StudentPlannedSection
from app.models.availability import SectionAvailability
from app.models.sections import CourseSection
from app.services.assistant.cards import meeting_lines
from app.services.assistant.contracts import term_label

WEBREG_URL = "https://sims.rutgers.edu/webreg/"
STEPS = ["Open WebReg", "Choose the term", "Paste the index numbers", "Add to schedule",
         "Submit"]


class SectionNotFound(LookupError):
    """No section with that index for that course in that term."""


def _minutes(military: str | None) -> int | None:
    from app.services.scheduling.meetings import minutes

    return minutes(military)


def _section(session: Session, term: str, course: str, index: str) -> CourseSection | None:
    return session.scalar(
        select(CourseSection)
        .join(CourseOffering, CourseOffering.id == CourseSection.offering_id)
        .join(Course, Course.id == CourseOffering.course_id)
        .where(CourseSection.term_code == term, CourseSection.index_number == index,
               Course.course_string == course)
        .options(selectinload(CourseSection.meetings), selectinload(CourseSection.instructors)))


def choose_section(session: Session, student: Student, *, term_code: str, course: str,
                   index_number: str, now: datetime | None = None) -> StudentPlannedSection:
    section = _section(session, term_code, course, index_number)
    if section is None:
        raise SectionNotFound(f"{course} has no section {index_number} in "
                              f"{term_label(term_code) or term_code}")
    row = session.scalar(select(StudentPlannedSection).where(
        StudentPlannedSection.student_id == student.id,
        StudentPlannedSection.term_code == term_code,
        StudentPlannedSection.course_string == course))
    if row is None:
        row = StudentPlannedSection(student_id=student.id, term_code=term_code,
                                    course_string=course, index_number=index_number)
        session.add(row)
    row.index_number = index_number
    row.section_number = section.section_number
    row.section_id = section.id
    row.source = "student_chosen"
    row.chosen_at = now or datetime.now(UTC)
    session.flush()
    return row


def remove_section(session: Session, student: Student, *, term_code: str, course: str) -> bool:
    row = session.scalar(select(StudentPlannedSection).where(
        StudentPlannedSection.student_id == student.id,
        StudentPlannedSection.term_code == term_code,
        StudentPlannedSection.course_string == course))
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def _meetings(section: CourseSection) -> list[dict]:
    return [{"day": m.meeting_day, "start_minute": _minutes(m.start_time_military),
             "end_minute": _minutes(m.end_time_military),
             "campus": m.campus_abbrev or m.campus_name,
             "kind": "async" if not m.meeting_day else "timed"} for m in section.meetings]


def webreg_panel(session: Session, student: Student, term_code: str,
                 planned: list[dict] | None = None) -> dict:
    """The term's chosen sections, ready to copy into WebReg."""
    rows = session.scalars(select(StudentPlannedSection).where(
        StudentPlannedSection.student_id == student.id,
        StudentPlannedSection.term_code == term_code)
        .order_by(StudentPlannedSection.course_string)).all()
    items, warnings, timed = [], [], []
    for row in rows:
        section = session.get(CourseSection, row.section_id) if row.section_id else None
        item = {"course": row.course_string, "index_number": row.index_number,
                "section_number": row.section_number, "title": "", "meetings": [],
                "open_status": "unknown", "warnings": []}
        if section is None:
            item["warnings"].append("This section is no longer published - choose another")
        else:
            course = section.offering.course if section.offering else None
            item["title"] = course.title if course else ""
            meetings = _meetings(section)
            item["meetings"] = meeting_lines(meetings)
            avail = session.scalar(select(SectionAvailability.status).where(
                SectionAvailability.section_id == section.id))
            item["open_status"] = avail or ("open" if section.open_status else "closed")
            if item["open_status"] == "closed":
                item["warnings"].append("Closed — consider a watch")
            if section.special_permission_add_code:
                item["warnings"].append("Special permission required"
                                        + (f": {section.special_permission_add_description}"
                                           if section.special_permission_add_description
                                           else ""))
            timed += [(row.course_string, m) for m in meetings
                      if m["day"] and m["start_minute"] is not None
                      and m["end_minute"] is not None]
        items.append(item)
    for i, (a, ma) in enumerate(timed):
        for b, mb in timed[i + 1:]:
            if a != b and ma["day"] == mb["day"] and ma["start_minute"] < mb["end_minute"] \
                    and mb["start_minute"] < ma["end_minute"]:
                msg = f"{a} and {b} overlap"
                if msg not in warnings:
                    warnings.append(msg)
    chosen = {r.course_string for r in rows}
    missing = [c for c in (planned or []) if c["course"] not in chosen]
    return {"term_code": term_code, "label": term_label(term_code), "items": items,
            "index_numbers": [i["index_number"] for i in items],
            "without_section": [{"course": c["course"], "title": c.get("title", "")}
                                for c in missing],
            "warnings": warnings, "steps": STEPS, "webreg_url": WEBREG_URL,
            "note": ("Coursenav does not sign in to WebReg or register you. Copy the index "
                     "numbers and add them in WebReg yourself.")}


__all__ = ["STEPS", "WEBREG_URL", "SectionNotFound", "choose_section", "remove_section",
           "webreg_panel"]
