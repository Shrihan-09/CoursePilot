"""A student's WebReg schedule PDF: staged, reviewed, then confirmed (Phase 7.2.2, 7.4).

```
upload -> extract -> normalize -> validate -> STAGING -> review -> confirm
```

Each row is matched by (term, index) against the term's published SOC
sections, falling back to (course, section) only when the print shows no
index. The course and section printed must agree with the section the index
names, or the row is a `conflict` (an index reused, a section changed). A row
with no section is `metadata_unavailable` (Prompt 04's "unrecognized") and is
never retargeted to another section.

A matched section is PRE-SELECTED but nothing is written until the student
confirms. Phase 7.4 (Prompt 04): confirmation then writes, in the caller's ONE
transaction,

  * the in-progress `student_course` row for that course and term (inserted;
    a planned row becomes in progress; one already in progress is skipped; a
    COMPLETED row for that term is a conflict the student must resolve);
  * the exact section as a `student_current_section` row (a different section
    already confirmed for that course needs explicit replacement);
  * the registration identity (`student_registration`), as before.

A course confirmed earlier for the term that a newer import does not list is
reported "not on your latest WebReg schedule - dropped?" and never deleted.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, timedelta

from sqlalchemy import select

from app.domain.provenance import SourceKind
from app.domain.webreg import PARSER_VERSION, parse_schedule
from app.models import Course, CourseOffering, CourseSection, Student, StudentCourse
from app.models.current_section import StudentCurrentSection
from app.models.schedule_import import ScheduleImport, StudentRegistration
from app.services.transcripts.extract import check_document

TTL = timedelta(days=7)


class ScheduleImportRefused(Exception):
    pass


def utc(value):
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def owned(session, student_id, import_id, *, lock=False):
    query = select(ScheduleImport).where(
        ScheduleImport.id == import_id, ScheduleImport.student_id == student_id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return session.scalar(query)


def _history_effect(session, student_id, row, course_id):
    """What confirming this row does to the record (Phase 7.4)."""
    record = session.scalar(select(StudentCourse).where(
        StudentCourse.student_id == student_id, StudentCourse.course_id == course_id,
        StudentCourse.term_code == row["term_code"]))
    if record is None:
        return "add_in_progress"
    if record.status == "in_progress":
        return "already_in_progress"
    if record.status == "planned":
        return "planned_to_in_progress"
    return "keep_completed" if row.get("keep_record") else "record_says_completed"


def _section_effect(session, student_id, row):
    """Whether a different section is already confirmed for this course and term."""
    existing = list(session.scalars(select(StudentCurrentSection).where(
        StudentCurrentSection.student_id == student_id,
        StudentCurrentSection.term_code == row["term_code"],
        StudentCurrentSection.course_string == row["course_string"],
        StudentCurrentSection.supplement_code == row.get("resolved_supplement", ""))))
    if not existing:
        return "add"
    if any(e.index_number == row.get("resolved_index") for e in existing):
        return "already_confirmed"
    return "replace" if row.get("replace_existing") else "differs"


def resolve_row(session, student_id, row):
    row = dict(row)
    row["issues"] = []
    row["section_id"] = None
    row.pop("resolved_index", None)
    row.pop("resolved_supplement", None)
    row.pop("history_effect", None)
    row.pop("section_effect", None)
    if row.get("excluded"):
        row["state"] = "excluded"
        row["match_status"] = "NEEDS_CONFIRMATION"
        return row
    query = (
        select(CourseSection, Course)
        .join(CourseOffering, CourseOffering.id == CourseSection.offering_id)
        .join(Course, Course.id == CourseOffering.course_id)
        .where(CourseSection.term_code == row["term_code"])
    )
    if row.get("index_number"):
        query = query.where(CourseSection.index_number == row["index_number"])
    else:
        query = query.where(
            Course.course_string == row["course_string"],
            CourseSection.section_number == row["section_number"],
        )
    matches = session.execute(query).all()
    consistent = [
        (section, course)
        for section, course in matches
        if course.course_string == row["course_string"]
        and section.section_number == row["section_number"]
    ]
    if matches and not consistent:
        row["state"], row["issues"] = "conflict", ["IDENTIFIERS_DISAGREE"]
    elif len(consistent) > 1:
        row["state"], row["issues"] = "ambiguous", ["MULTIPLE_SOC_MATCHES"]
    elif consistent:
        section, matched_course = consistent[0]
        row["state"] = "matched"
        row["section_id"] = str(section.id)
        # Preserve extracted identity; a fallback match may supply its exact index.
        row["resolved_index"] = section.index_number
        row["resolved_supplement"] = matched_course.supplement_code
        row["course_id"] = str(matched_course.id)
        # Phase 7.4: what confirmation will do to the record, shown BEFORE it does.
        row["history_effect"] = _history_effect(session, student_id, row, matched_course.id)
        row["section_effect"] = (None if row["history_effect"] == "keep_completed"
                                 else _section_effect(session, student_id, row))
    else:
        row["state"], row["issues"] = "metadata_unavailable", ["SOC_METADATA_UNAVAILABLE"]
    existing = session.scalar(
        select(StudentRegistration).where(
            StudentRegistration.student_id == student_id,
            StudentRegistration.term_code == row["term_code"],
            StudentRegistration.course_string == row["course_string"],
            StudentRegistration.section_number == row["section_number"],
            StudentRegistration.supplement_code == row.get("resolved_supplement", ""),
        )
    )
    actual_index = row.get("resolved_index") or row.get("index_number")
    if (
        existing is not None
        and existing.index_number != actual_index
        and not row.get("replace_existing")
    ) or row.get("section_effect") == "differs":
        row["state"], row["issues"] = "conflict", ["CONFIRM_REPLACE_EXISTING"]
    if row.get("history_effect") == "record_says_completed":
        row["state"], row["issues"] = "conflict", [*row["issues"], "RECORD_SAYS_COMPLETED"]
    row["match_status"] = {
        "matched": "EXACT_MATCH",
        "metadata_unavailable": "NO_MATCH",
        "ambiguous": "MULTIPLE_MATCHES",
        "conflict": "NEEDS_CONFIRMATION",
    }[row["state"]]
    return row


def resolve_rows(session, student_id, rows):
    result = [resolve_row(session, student_id, r) for r in rows]
    seen = set()
    for row in result:
        if row.get("excluded"):
            continue
        key = (row["term_code"], row["course_string"], row["section_number"])
        if key in seen:
            row["state"], row["issues"] = "conflict", ["DUPLICATE_REGISTRATION"]
            row["match_status"] = "NEEDS_CONFIRMATION"
        seen.add(key)
    return result


def possibly_dropped(session, student_id, term_code, rows):
    """Courses confirmed earlier for this term that these rows do not list:
    "not on your latest WebReg schedule - dropped?". Reported, never deleted."""
    listed = {r["course_string"] for r in rows if not r.get("excluded")}
    out: dict[str, dict] = {}
    for model, source in ((StudentRegistration, "webreg_import"),
                          (StudentCurrentSection, "confirmed_section")):
        for record in session.scalars(select(model).where(
                model.student_id == student_id, model.term_code == term_code)):
            if record.course_string not in listed:
                out.setdefault(record.course_string, {
                    "term_code": term_code, "course_string": record.course_string,
                    "section_number": record.section_number,
                    "index_number": record.index_number, "source": source})
    return [out[k] for k in sorted(out)]


def latest_import_courses(session, student_id, term_code):
    """Courses listed by the newest CONFIRMED import of this term, or None."""
    for imp in session.scalars(select(ScheduleImport).where(
            ScheduleImport.student_id == student_id, ScheduleImport.state == "confirmed")
            .order_by(ScheduleImport.confirmed_at.desc())):
        rows = [r for r in imp.rows or [] if r.get("term_code") == term_code]
        if rows:
            return {r["course_string"] for r in rows if not r.get("excluded")}
    return None


def ready(rows):
    return (
        bool(rows)
        and any(not r.get("excluded") for r in rows)
        and all(r["state"] in {"matched", "metadata_unavailable", "excluded"} for r in rows)
    )


def stage(session, student, data, *, extractor, max_bytes, now):
    check_document(data, max_bytes)
    digest = hashlib.sha256(data).hexdigest()
    active = list(
        session.scalars(
            select(ScheduleImport).where(
                ScheduleImport.student_id == student.id, ScheduleImport.state == "extracted"
            )
        )
    )
    for imp in active:
        if utc(imp.expires_at) <= now:
            imp.rows, imp.state = [], "expired"
        elif imp.content_sha256 == digest:
            return imp
    if sum(utc(i.expires_at) > now for i in active) >= 3:
        raise ScheduleImportRefused("TOO_MANY_OPEN_IMPORTS")
    # No disk storage at all: bounded bytes go directly to isolated extractor.
    extracted = extractor(data)
    parsed = parse_schedule(extracted.pages, extracted.layout)
    rows = [
        {
            **r,
            "id": str(uuid.uuid4()),
            "excluded": False,
            "extracted": dict(r),
            "replace_existing": False,
        }
        for r in parsed
    ]
    imp = ScheduleImport(
        student_id=student.id,
        content_sha256=digest,
        parser_version=PARSER_VERSION,
        expires_at=now + TTL,
        state="extracted",
        version=1,
        rows=resolve_rows(session, student.id, rows),
    )
    session.add(imp)
    session.flush()
    return imp


def editable(imp, expected_version, now):
    if imp.state != "extracted" or utc(imp.expires_at) <= now:
        raise ScheduleImportRefused("IMPORT_NOT_EDITABLE_OR_EXPIRED")
    if imp.version != expected_version:
        raise ScheduleImportRefused("IMPORT_VERSION_STALE")


def edit(session, imp, row_id, changes, *, expected_version, now):
    editable(imp, expected_version, now)
    rows = [dict(r) for r in imp.rows]
    row = next((r for r in rows if r["id"] == str(row_id)), None)
    if row is None:
        raise ScheduleImportRefused("ROW_NOT_FOUND")
    row.update(changes)
    row.pop("resolved_index", None)
    row.pop("resolved_supplement", None)
    imp.rows = resolve_rows(session, imp.student_id, rows)
    imp.version += 1
    session.flush()
    return imp


def confirm(session, imp, *, expected_version, now):
    """Write the reviewed rows. Everything happens in the caller's single
    transaction: history, current sections and registrations together, or
    nothing (any exception rolls all of it back)."""
    if imp.state == "confirmed":
        return imp
    editable(imp, expected_version, now)
    updated = resolve_rows(session, imp.student_id, imp.rows)
    if updated != imp.rows:
        # Caller rolls back on refusal: review endpoint can explicitly refresh.
        raise ScheduleImportRefused("SOC_CHANGED_REVIEW_AGAIN")
    if not ready(updated):
        raise ScheduleImportRefused("ROWS_NEED_REVIEW")
    student = None
    history_changed = False
    confirmed_rows = []
    for row in updated:
        row = dict(row)
        if row.get("excluded"):
            confirmed_rows.append(row)
            continue
        outcome = {"history": None, "section": None}
        if row["state"] == "matched" and row.get("course_id"):
            student = student or session.get(Student, imp.student_id)
            record = _write_history(session, student, row, now)
            outcome["history"] = row["history_effect"]
            history_changed |= row["history_effect"] in ("add_in_progress",
                                                         "planned_to_in_progress")
            if record is not None:
                outcome["section"] = _write_section(session, student, row, record, now)
        _write_registration(session, imp, row, now)
        row["outcome"] = outcome
        confirmed_rows.append(row)
    imp.rows = confirmed_rows
    imp.state, imp.confirmed_at = "confirmed", now
    session.flush()
    if history_changed:
        from app.services.onboarding import invalidate_audit
        invalidate_audit(session, imp.student_id)
    return imp


def _write_history(session, student, row, now):
    """The in-progress record for this course and term, or None when the
    student kept a completed record (then nothing else is written for it)."""
    effect = row["history_effect"]
    if effect == "keep_completed":
        return None
    course_id = uuid.UUID(row["course_id"])
    record = session.scalar(select(StudentCourse).where(
        StudentCourse.student_id == student.id, StudentCourse.course_id == course_id,
        StudentCourse.term_code == row["term_code"]))
    if record is None:
        record = StudentCourse(student_id=student.id, course_id=course_id,
                               term_code=row["term_code"], status="in_progress", grade=None,
                               credits_earned=None, credit_origin="rutgers",
                               source_kind=SourceKind.STUDENT_UPLOADED_SCHEDULE.value)
        session.add(record)
        session.flush()
    elif record.status == "planned":
        record.status = "in_progress"
        record.source_kind = SourceKind.STUDENT_UPLOADED_SCHEDULE.value
    return record


def _write_section(session, student, row, record, now):
    """The exact section as a current-section confirmation (7.2.1's table)."""
    from app.services.scheduling.current import _candidates

    _, by_course = _candidates(session, student, row["term_code"], [row["course_string"]])
    match = next((c for c in by_course.get(row["course_string"], [])
                  if c.index_number == row.get("resolved_index")
                  and str(c.section_id) == row["section_id"]), None)
    if match is None:
        return "section_unavailable"
    current = session.scalar(select(StudentCurrentSection).where(
        StudentCurrentSection.student_course_id == record.id,
        StudentCurrentSection.component == match.component,
        StudentCurrentSection.supplement_code == match.supplement_code))
    if current is not None and current.index_number == match.index_number:
        return "already_confirmed"
    outcome = "replaced" if current is not None else "added"
    if current is None:
        current = StudentCurrentSection(student_id=student.id, student_course_id=record.id,
                                        component=match.component)
        session.add(current)
    current.term_code = row["term_code"]
    current.course_string = row["course_string"]
    current.section_number = match.section_number
    current.supplement_code = match.supplement_code
    current.index_number = match.index_number
    current.section_id = match.section_id
    current.source = "webreg_schedule_import"
    current.confirmed_at = now
    session.flush()
    return outcome


def _write_registration(session, imp, row, now):
    record = session.scalar(
        select(StudentRegistration).where(
            StudentRegistration.student_id == imp.student_id,
            StudentRegistration.term_code == row["term_code"],
            StudentRegistration.course_string == row["course_string"],
            StudentRegistration.section_number == row["section_number"],
            StudentRegistration.supplement_code == row.get("resolved_supplement", ""),
        )
    )
    if record is None:
        record = StudentRegistration(
            student_id=imp.student_id,
            term_code=row["term_code"],
            course_string=row["course_string"],
            section_number=row["section_number"],
        )
        session.add(record)
    record.supplement_code = row.get("resolved_supplement", "")
    record.index_number = row.get("resolved_index") or row.get("index_number")
    record.section_id = uuid.UUID(row["section_id"]) if row.get("section_id") else None
    record.source, record.import_id, record.confirmed_at = "student_schedule_pdf", imp.id, now
