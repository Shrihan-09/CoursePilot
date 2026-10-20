"""Student-controlled schedule evidence; never writes academic history."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, timedelta

from sqlalchemy import select

from app.domain.webreg import PARSER_VERSION, parse_schedule
from app.models import Course, CourseOffering, CourseSection
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


def resolve_row(session, student_id, row):
    row = dict(row)
    row["issues"] = []
    row["section_id"] = None
    row.pop("resolved_index", None)
    row.pop("resolved_supplement", None)
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
    ):
        row["state"], row["issues"] = "conflict", ["CONFIRM_REPLACE_EXISTING"]
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
    if imp.state == "confirmed":
        return imp
    editable(imp, expected_version, now)
    updated = resolve_rows(session, imp.student_id, imp.rows)
    if updated != imp.rows:
        # Caller rolls back on refusal: review endpoint can explicitly refresh.
        raise ScheduleImportRefused("SOC_CHANGED_REVIEW_AGAIN")
    if not ready(updated):
        raise ScheduleImportRefused("ROWS_NEED_REVIEW")
    for row in updated:
        if row.get("excluded"):
            continue
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
    imp.state, imp.confirmed_at = "confirmed", now
    session.flush()
    return imp
