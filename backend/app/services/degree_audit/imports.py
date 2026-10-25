"""Degree Navigator audit import: staging, review, reconciliation, confirmation (Phase 7.4).

```
upload -> extract (child process: text + status icons) -> parse -> normalize
       -> validate -> STAGING -> reconciliation -> student review -> confirm
```

Mirrors `app.services.transcripts.imports` and keeps its guarantees:

* the raw PDF is deleted as soon as extraction ends, success or failure; only
  the extracted rows and the file hash remain;
* staged rows expire after `STAGING_TTL`; an old `processing` row is failed;
* original extracted values sit in their own columns and are never
  overwritten; a row's state is DERIVED from its issues;
* confirmation names the version the student reviewed, re-checks everything
  in one transaction, and a second confirmation writes nothing.

Courses are NOT written from here. Every completed or current course DN lists
becomes a row of a linked `transcript_import` (source_document
'degree_navigator_audit') and goes through the existing transcript
normalization, conflict resolution and `confirm_import` - one path for history.

What a confirmed DN import adds is the REQUIREMENT STATUS record
(`degree_audit_requirement_row`), which `overlay.py` turns into the status
planning uses. Grades are never logged; the name and RUID are never read.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.observability import redact_id
from app.domain.degree_audit import ParsedBlock, ParsedCourse, parse_audit
from app.domain.history import Issue, IssueSeverity, parse_credits
from app.models import Program, ProgramVersion, School, Student
from app.models.degree_audit import (
    DegreeAuditCourseRow,
    DegreeAuditImport,
    DegreeAuditRequirementRow,
    DnRowState,
)
from app.models.onboarding import ImportState, RowState, TranscriptImport, TranscriptImportRow
from app.services.degree_audit.mapping import DnMapping, TrackCheck, check_track, load_mappings
from app.services.onboarding import ProfileMissing, invalidate_audit, lock_student
from app.services.transcripts import imports as transcripts
from app.services.transcripts.extract import ExtractionFailed, Extractor, check_document
from app.services.transcripts.imports import (
    DN_AUDIT_SOURCE,
    MAX_OPEN_IMPORTS,
    STAGING_TTL,
    ImportNotFound,
    ImportRefused,
    effective_state,
)
from app.services.transcripts.storage import TranscriptStorage

logger = logging.getLogger("coursepilot.degree_audit")

EXTRACTOR_VERSION = "dn-audit-1"

#: Refusal messages a student sees (decision 8 of the 7.4 answers names the first).
FAILURE_MESSAGES = {
    "PDF_NO_TEXT_LAYER": ("This PDF has no text. In Chrome, use Print → Destination: Save as "
                          "PDF (not Microsoft Print to PDF)."),
    "NOT_A_DEGREE_AUDIT": ("This PDF is not a Degree Navigator audit. In Degree Navigator open "
                           "your audit, then use Chrome's Print → Save as PDF."),
    "PDF_ENCRYPTED": "This PDF is password-protected.",
    "PDF_TOO_MANY_PAGES": "This PDF has more pages than a Degree Navigator audit.",
    "PDF_TEXT_TOO_LARGE": "This PDF holds more text than a Degree Navigator audit.",
    "UNSUPPORTED_LAYOUT": "This PDF's page layout is not one CoursePilot can read.",
    "PDF_UNREADABLE": "CoursePilot could not read this PDF.",
    "PDF_EXTRACTION_TIMEOUT": "Reading this PDF took too long.",
    "PDF_EXTRACTION_CRASHED": "CoursePilot could not read this PDF.",
    "PROCESSING_INTERRUPTED": "Reading this PDF was interrupted; upload it again.",
}

#: Parse problem -> (severity, message) for requirement and course rows.
_PROBLEMS = {
    "ICON_MISSING": "No status icon was found beside this requirement.",
    "ICON_UNKNOWN": ("The status icon beside this requirement matches none of Degree "
                     "Navigator's legend icons."),
    "ICONS_MULTIPLE": "More than one status icon sits beside this requirement.",
    "STATUS_TEXT_MISMATCH": ("The status icon disagrees with the requirement's \"Needs\" line. "
                             "Check this requirement in Degree Navigator."),
    "ALLOCATION_COUNT_MISMATCH": ("Degree Navigator's \"Completed\" count differs from the "
                                  "courses read under this requirement; a course line may have "
                                  "been missed."),
    "COMPLETED_COUNT_MISMATCH": ("The audit's \"Completed Requirements\" count differs from the "
                                 "statuses read; one status may have been misread."),
    "COURSE_ROWS_NEED_REVIEW": "A course line under this requirement could not be read.",
    "GOAL_CODES_AMBIGUOUS": "This goal set names more than one goal code.",
    "PAGES_MISSING": "Pages of this audit are missing from the PDF.",
    "COURSE_LINE_UNREADABLE": "A course code was found but the rest of its line could not be read.",
    "READING_ORDER_UNRELIABLE": ("Course details were read on a line without a course code (the "
                                 "columns may have run together)."),
    "UNCLASSIFIED_COURSE_ROW": ("CoursePilot does not recognise this kind of course row (transfer, "
                                "AP or an exception?). Its text is kept; exclude it or check it "
                                "against your transcript."),
}
_BLOCK_WIDE = ("PAGES_MISSING",)
_STATUS_OF_ICON = {"fulfilled": "satisfied", "not_fulfilled": "not_satisfied",
                   "in_progress_or_planned": "in_progress_or_planned"}
DN_STATUSES_EDITABLE = ("satisfied", "in_progress_or_planned", "not_satisfied")


# --------------------------------------------------------------------------
# the student's track
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Track:
    key: str
    name: str
    catalog_year: str
    school_core_keys: frozenset[str]


def student_track(session: Session, student: Student) -> Track:
    from app.services.academic_context import school_requirement_sets
    from app.services.programs import program_key

    version, program, school = session.execute(
        select(ProgramVersion, Program, School)
        .join(Program, Program.id == ProgramVersion.program_id)
        .join(School, School.id == Program.school_id)
        .where(ProgramVersion.id == student.program_version_id)).one()
    key = program_key(school.code, program.code, program.degree_type, program.variant)
    cores = frozenset(s.code for s in school_requirement_sets(session, version))
    return Track(key, program.name, student.catalog_year, cores)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


def get_import(session: Session, student_id: uuid.UUID, import_id: uuid.UUID, *,
               lock: bool = False) -> DegreeAuditImport:
    """The owner's import; another student's is 'not found', never 'forbidden'."""
    q = select(DegreeAuditImport).where(DegreeAuditImport.id == import_id,
                                        DegreeAuditImport.student_id == student_id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    imp = session.scalar(q)
    if imp is None:
        raise ImportNotFound("No such Degree Navigator import.")
    return imp


def list_imports(session: Session, student_id: uuid.UUID) -> list[DegreeAuditImport]:
    return list(session.scalars(select(DegreeAuditImport).where(
        DegreeAuditImport.student_id == student_id)
        .order_by(DegreeAuditImport.created_at.desc(), DegreeAuditImport.block_index).limit(50)))


def linked_transcript(session: Session, imp: DegreeAuditImport) -> TranscriptImport | None:
    return session.get(TranscriptImport, imp.transcript_import_id) \
        if imp.transcript_import_id else None


@dataclass(frozen=True)
class Readiness:
    ready: bool
    blocking: int
    requirement_counts: dict
    course_counts: dict
    history_counts: dict
    track_blocks: bool


def readiness(session: Session, imp: DegreeAuditImport, now: datetime) -> Readiness:
    req_counts: dict = {}
    course_counts: dict = {}
    blocking = 0
    for row in imp.requirement_rows:
        key = "excluded" if row.excluded else row.state
        req_counts[key] = req_counts.get(key, 0) + 1
        blocking += not row.excluded and row.state == DnRowState.NEEDS_REVIEW
    for row in imp.course_rows:
        key = "excluded" if row.excluded else row.state
        course_counts[key] = course_counts.get(key, 0) + 1
        blocking += not row.excluded and row.state == "needs_review"
    timp = linked_transcript(session, imp)
    history = transcripts.readiness(timp) if timp is not None else None
    if history is not None:
        blocking += history.blocking_rows
    track_blocks = imp.track_check == "mismatch"
    return Readiness(
        ready=effective_state(imp, now) == ImportState.EXTRACTED and blocking == 0
        and not track_blocks, blocking=blocking, requirement_counts=req_counts,
        course_counts=course_counts, history_counts=history.counts if history else {},
        track_blocks=track_blocks)


# --------------------------------------------------------------------------
# normalization
# --------------------------------------------------------------------------


def _issue(code: str, severity: IssueSeverity = IssueSeverity.NEEDS_REVIEW,
           message: str | None = None, field: str | None = None) -> dict:
    return Issue(code, field, severity,
                 message or _PROBLEMS.get(code, "Part of this row could not be read.")).as_dict()


def normalize_requirement_row(row: DegreeAuditRequirementRow, mapping: DnMapping | None) -> None:
    """Map to a node, derive issues and state. Idempotent."""
    issues = []
    if not row.edited:
        issues += [_issue(code) for code in row.extraction_problems or []]
    entry = None
    if mapping is not None:
        entry = mapping.requirement(row.dn_heading or "") if row.kind == "requirement" \
            else mapping.goal(row.dn_code or "")
    row.requirement_code = entry.requirement_code if entry is not None and entry.usable else None
    row.mapping_review_status = entry.review_status if entry is not None else None
    if row.requirement_code is None:
        reason = (entry.reason if entry is not None and entry.reason else
                  "No reviewed CoursePilot requirement corresponds to this Degree Navigator "
                  "row, so it is shown but not used; CoursePilot's own estimate applies.")
        issues.append(_issue("DN_REQUIREMENT_UNMAPPED", IssueSeverity.INFO, reason))
    elif row.mapping_review_status != "reviewed":
        issues.append(_issue("DN_MAPPING_PENDING_REVIEW", IssueSeverity.INFO,
                             "The mapping of this row to a CoursePilot requirement has not been "
                             "reviewed by a person yet."))
    if row.dn_status == "unknown":
        issues.append(_issue("DN_STATUS_UNKNOWN", message=(
            "Degree Navigator's status for this row could not be read. Set it from your "
            "Degree Navigator audit, or exclude the row.")))
    severities = {i["severity"] for i in issues}
    if row.requirement_code is None:
        row.state = DnRowState.UNMAPPED                  # kept and shown, never blocks
    elif IssueSeverity.NEEDS_REVIEW.value in severities:
        row.state = DnRowState.NEEDS_REVIEW
    else:
        row.state = DnRowState.MATCHED
    row.issues = issues


def normalize_course_row(row: DegreeAuditCourseRow) -> None:
    issues = [_issue(code) for code in row.extraction_problems or []]
    if row.kind == "placement":
        issues.append(_issue("DN_PLACEMENT_RECORD", IssueSeverity.INFO,
                             "A placement record, not a course; it is not added to your history."))
    elif row.kind == "planned":
        issues.append(_issue("DN_PLANNED_COURSE", IssueSeverity.INFO,
                             "A course in your Degree Navigator plan; plans are not history."))
    if any(i["severity"] == IssueSeverity.NEEDS_REVIEW.value for i in issues) \
            or row.kind == "unclassified":
        row.state = "needs_review"
    elif row.kind in ("placement", "planned"):
        row.state = "info"
    else:
        row.state = "matched"
    row.issues = issues


# --------------------------------------------------------------------------
# upload
# --------------------------------------------------------------------------


def _as_of(block: ParsedBlock, now: datetime) -> tuple[datetime, str]:
    """Chrome's print timestamp (Eastern - Rutgers New Brunswick), else upload time."""
    from app.core.grade_calendar import EASTERN

    if block.printed_at:
        try:
            parsed = datetime.strptime(block.printed_at, "%m/%d/%y, %I:%M %p")
            return parsed.replace(tzinfo=EASTERN), "print_header"
        except ValueError:
            pass
    return now, "upload_time"


def _course_payload(c: ParsedCourse) -> dict:
    return {"course": c.course_code, "term_code": c.term_code, "kind": c.kind}


def upload_degree_audit(session: Session, account_id: uuid.UUID, data: bytes, *,
                        storage: TranscriptStorage, extractor: Extractor, now: datetime,
                        max_bytes: int, mapping_root=None) -> tuple[list[DegreeAuditImport], bool]:
    """(imports - one per degree block, created). Re-uploading bytes that are
    already open returns those imports; nothing is parsed twice."""
    check_document(data, max_bytes)
    digest = hashlib.sha256(data).hexdigest()
    student = lock_student(session, account_id)
    if student is None:
        raise ProfileMissing("No academic record exists for this account.")
    same = list(session.scalars(select(DegreeAuditImport).where(
        DegreeAuditImport.student_id == student.id, DegreeAuditImport.content_sha256 == digest,
        DegreeAuditImport.state.in_((ImportState.PROCESSING, ImportState.EXTRACTED)))
        .order_by(DegreeAuditImport.block_index)))
    live = [i for i in same if effective_state(i, now) in (ImportState.PROCESSING,
                                                           ImportState.EXTRACTED)]
    if live:
        session.commit()
        return live, False
    for stale in same:
        _retire(session, stale, now)
    session.flush()
    open_docs = {i.content_sha256 for i in session.scalars(select(DegreeAuditImport).where(
        DegreeAuditImport.student_id == student.id,
        DegreeAuditImport.state.in_((ImportState.PROCESSING, ImportState.EXTRACTED))))
        if effective_state(i, now) in (ImportState.PROCESSING, ImportState.EXTRACTED)}
    if len(open_docs) >= MAX_OPEN_IMPORTS:
        raise ImportRefused("TOO_MANY_OPEN_IMPORTS",
                            "Confirm or discard an open Degree Navigator import first.")

    imp = DegreeAuditImport(student_id=student.id, state=ImportState.PROCESSING,
                            content_sha256=digest, byte_size=len(data), block_index=0,
                            block_count=1, warnings=[], extractor_version=EXTRACTOR_VERSION)
    session.add(imp)
    session.flush()
    imp.storage_key = storage.put(data)
    session.commit()
    import_id, student_id = imp.id, student.id

    failure = None
    parsed = text = None
    try:
        text = extractor(storage.get(imp.storage_key))
        parsed = parse_audit(text.layout or [])
        if not parsed.blocks:
            failure = "NOT_A_DEGREE_AUDIT"
    except ExtractionFailed as exc:
        failure = exc.code
    except Exception:
        # Never the exception's text: it can quote the document.
        logger.error("dn_extraction_crashed", extra={"import": redact_id(import_id)})
        failure = "PDF_EXTRACTION_CRASHED"
    finally:
        _drop_document(session, storage, imp)

    imp = session.get(DegreeAuditImport, import_id)
    if failure is not None:
        imp.state, imp.failure_code = ImportState.FAILED, failure
        session.commit()
        logger.info("dn_import_failed", extra={"import": redact_id(import_id),
                                               "outcome": failure})
        return [imp], True

    student = session.get(Student, student_id)
    track = student_track(session, student)
    mappings = load_mappings(track.catalog_year, mapping_root)
    imports = []
    for block in parsed.blocks:
        if block.index == 0:
            target = imp
        else:
            target = DegreeAuditImport(student_id=student_id, state=ImportState.PROCESSING,
                                       content_sha256=digest, byte_size=len(data),
                                       block_index=block.index, warnings=[],
                                       extractor_version=EXTRACTOR_VERSION)
            session.add(target)
            session.flush()
        target.block_count = len(parsed.blocks)
        target.page_count = text.page_count
        _stage_block(session, target, block, student, track, mappings, now)
        imports.append(target)
    session.commit()
    logger.info("dn_import_extracted", extra={
        "import": redact_id(import_id), "blocks": len(imports),
        "requirements": sum(len(i.requirement_rows) for i in imports)})
    return imports, True


def _stage_block(session: Session, imp: DegreeAuditImport, block: ParsedBlock, student: Student,
                 track: Track, mappings: list[DnMapping], now: datetime) -> None:
    imp.dn_subject = block.subject and block.subject[:255]
    imp.dn_version_label = block.version_label
    imp.dn_degree_id = block.degree_id
    imp.dn_report_date = block.report_date
    imp.printed_at_text = block.printed_at
    imp.completed_requirements_text = block.completed_requirements
    imp.as_of, imp.as_of_source = _as_of(block, now)
    imp.warnings = list(block.warnings)
    check: TrackCheck = check_track(mappings, block.subject, block.version_label, track.key,
                                    track.name, set(track.school_core_keys))
    imp.track_check, imp.track_message = check.result, check.message
    mapping = check.mapping
    imp.mapping_key = mapping.program_key if mapping else (
        check.mapping.program_key if check.mapping else None)
    imp.mapping_version = mapping.mapping_version if mapping else None
    block_wide = [w for w in block.warnings if w in _BLOCK_WIDE]

    index = 0
    for req in block.requirements:
        parent = index
        courses = [{"course": c.course_code, "term_code": c.term_code, "kind": c.kind}
                   for c in req.courses if c.course_code]
        # A course current in the goal set (icon) is "behind" the requirement too.
        for goal in req.goals:
            for code, icon in goal.courses:
                if not any(c["course"] == code for c in courses):
                    courses.append({"course": code, "term_code": None, "icon": icon,
                                    "kind": None})
        row = DegreeAuditRequirementRow(
            row_index=index, kind="requirement", page_number=req.page, line_number=req.line,
            dn_number=req.number, dn_heading=req.heading_text, extracted_title=req.title,
            extracted_codes=list(req.codes), extracted_icon=req.icon,
            extracted_total=req.total, extracted_completed=req.completed,
            extracted_needs=req.needs, extracted_courses=courses,
            extraction_problems=[*req.problems, *block_wide],
            dn_status=_STATUS_OF_ICON.get(req.icon, "unknown"), issues=[], cp_courses=[],
            state=DnRowState.NEEDS_REVIEW)
        normalize_requirement_row(row, mapping)
        imp.requirement_rows.append(row)
        index += 1
        terms = {c.course_code: c.term_code for c in req.courses}
        for goal in req.goals:
            icons = [icon for _, icon in goal.courses]
            if not goal.courses:
                status, derived = "not_satisfied", "derived:no_course"
            elif "unknown" in icons or "missing" in icons:
                status, derived = "unknown", "derived:icon_unreadable"
            elif "completed" in icons:
                status, derived = "satisfied", "derived:completed_course"
            else:
                status, derived = "in_progress_or_planned", "derived:current_or_planned"
            grow = DegreeAuditRequirementRow(
                row_index=index, kind="goal", parent_row_index=parent, page_number=req.page,
                line_number=req.line, dn_number=req.number, dn_code=goal.code or None,
                extracted_title=goal.title, extracted_codes=[goal.code] if goal.code else [],
                extracted_icon=derived,
                extracted_courses=[{"course": c, "term_code": terms.get(c), "icon": i,
                                    "kind": None} for c, i in goal.courses],
                extraction_problems=[*goal.problems, *block_wide], dn_status=status,
                issues=[], cp_courses=[], state=DnRowState.NEEDS_REVIEW)
            normalize_requirement_row(grow, mapping)
            imp.requirement_rows.append(grow)
            index += 1

    all_courses = [c for r in block.requirements for c in r.courses] + block.courses
    for n, c in enumerate(all_courses):
        crow = DegreeAuditCourseRow(
            row_index=n, list_kind=c.list_kind, requirement_number=c.requirement_number,
            page_number=c.page, line_number=c.line, raw_text=c.raw_text[:500],
            extracted_course_code=c.course_code, extracted_credits=c.credits,
            extracted_term=c.term_label, extracted_grade=c.grade and c.grade[:16],
            kind=c.kind, extraction_problems=list(c.problems), issues=[], state="matched")
        normalize_course_row(crow)
        imp.course_rows.append(crow)
    session.flush()
    _stage_history(session, imp, student, now)
    imp.state = ImportState.EXTRACTED
    imp.extracted_at = now
    imp.expires_at = now + STAGING_TTL
    session.flush()
    from app.services.degree_audit.reconciliation import reconcile
    reconcile(session, student, imp, now)


def _stage_history(session: Session, imp: DegreeAuditImport, student: Student,
                   now: datetime) -> None:
    """One staged history row per (course, term) DN lists as taken or current."""
    timp = TranscriptImport(
        student_id=student.id, state=ImportState.EXTRACTED,
        content_sha256=hashlib.sha256(f"{imp.content_sha256}:dn:{imp.block_index}".encode())
        .hexdigest(), byte_size=imp.byte_size, page_count=imp.page_count,
        parser_version=EXTRACTOR_VERSION, warnings=[], term_summaries=[],
        source_document=DN_AUDIT_SOURCE, extracted_at=now, expires_at=now + STAGING_TTL)
    session.add(timp)
    session.flush()
    imp.transcript_import_id = timp.id
    by_key: dict = {}
    for crow in imp.course_rows:
        if crow.kind not in ("course", "current") or crow.state == "needs_review":
            continue
        term = _term_code(crow)
        key = (crow.extracted_course_code, term)
        trow = by_key.get(key)
        if trow is None:
            problems = ["DN_CURRENT_COURSE"] if crow.kind == "current" else []
            credits, _ = parse_credits(crow.extracted_credits)
            trow = TranscriptImportRow(
                row_index=len(by_key), page_number=crow.page_number,
                line_number=crow.line_number, section="term", raw_text=crow.raw_text,
                extracted_course_code=crow.extracted_course_code,
                extracted_term=crow.extracted_term, extracted_credits=crow.extracted_credits,
                extracted_grade=(crow.extracted_grade or None)
                and crow.extracted_grade[:8], extraction_problems=problems,
                course_code=crow.extracted_course_code, supplement_code=None, term_code=term,
                status="in_progress" if crow.kind == "current" else "completed",
                grade=None if crow.kind == "current" else crow.extracted_grade,
                credits_earned=credits, credit_origin="rutgers", excluded=False, edited=False,
                issues=[], state=RowState.NEEDS_REVIEW)
            timp.rows.append(trow)
            by_key[key] = (trow, crow)
        else:
            trow, first = trow
            if (first.extracted_grade, first.extracted_credits, first.kind) != \
                    (crow.extracted_grade, crow.extracted_credits, crow.kind) \
                    and "DN_ROWS_DISAGREE" not in trow.extraction_problems:
                trow.extraction_problems = [*trow.extraction_problems, "DN_ROWS_DISAGREE"]
    session.flush()
    links = {k: v[0].id for k, v in by_key.items()}
    for crow in imp.course_rows:
        crow.transcript_row_id = links.get((crow.extracted_course_code, _term_code(crow)))
    transcripts.renormalize(session, timp)


def _term_code(crow: DegreeAuditCourseRow) -> str | None:
    from app.domain.transcript import term_code_for

    parts = (crow.extracted_term or "").split()
    if len(parts) == 2 and parts[1].isdigit():
        try:
            return term_code_for(parts[0], parts[1])
        except KeyError:
            return None
    return None


def _retire(session: Session, imp: DegreeAuditImport, now: datetime) -> None:
    if imp.state == ImportState.PROCESSING:
        imp.state, imp.failure_code = ImportState.FAILED, "PROCESSING_INTERRUPTED"
    else:
        _clear(session, imp, now)


def _clear(session: Session, imp: DegreeAuditImport, now: datetime) -> None:
    """Discard: staged rows (a copy of the student's record) are deleted."""
    timp = linked_transcript(session, imp)
    if timp is not None and timp.state in (ImportState.EXTRACTED, ImportState.PROCESSING):
        timp.rows.clear()
        timp.state, timp.discarded_at = ImportState.DISCARDED, now
    imp.requirement_rows.clear()
    imp.course_rows.clear()
    imp.state, imp.discarded_at = ImportState.DISCARDED, now


def _drop_document(session: Session, storage: TranscriptStorage, imp: DegreeAuditImport) -> None:
    key = imp.storage_key
    if key is None:
        return
    try:
        storage.delete(key)
    except Exception:
        logger.warning("dn_document_delete_failed", extra={"import": redact_id(imp.id)})
        return
    session.execute(update(DegreeAuditImport).where(DegreeAuditImport.id == imp.id)
                    .values(storage_key=None).execution_options(synchronize_session="fetch"))
    session.commit()


# --------------------------------------------------------------------------
# review
# --------------------------------------------------------------------------


def _open(session: Session, account_id: uuid.UUID, import_id: uuid.UUID, now: datetime):
    student = lock_student(session, account_id)
    if student is None:
        raise ProfileMissing("No academic record exists for this account.")
    imp = get_import(session, student.id, import_id, lock=True)
    state = effective_state(imp, now)
    if state != ImportState.EXTRACTED:
        raise ImportRefused("IMPORT_NOT_EDITABLE",
                            f"This import is {state}; it can no longer be changed.")
    return student, imp


def resolve_row(session: Session, account_id: uuid.UUID, import_id: uuid.UUID,
                row_id: uuid.UUID, changes: dict, *, expected_version: int,
                now: datetime) -> DegreeAuditImport:
    """Resolve one staged row - a requirement row (exclude it, or state its
    status as Degree Navigator shows it), a DN course line (exclude it), or a
    staged history row (the transcript path's corrections and conflict
    resolution). Bumps the import's version."""
    student, imp = _open(session, account_id, import_id, now)
    if imp.version != expected_version:
        raise ImportRefused("IMPORT_VERSION_STALE",
                            "This import changed since you loaded it; reload and review again.")
    req = next((r for r in imp.requirement_rows if r.id == row_id), None)
    course = next((r for r in imp.course_rows if r.id == row_id), None)
    if req is not None:
        if "dn_status" in changes and changes["dn_status"] is not None:
            if changes["dn_status"] not in DN_STATUSES_EDITABLE:
                raise ImportRefused("DN_STATUS_INVALID", "Status must be satisfied, "
                                    "in_progress_or_planned or not_satisfied.")
            req.dn_status = changes["dn_status"]
            req.edited = True
        if changes.get("excluded") is not None:
            req.excluded = bool(changes["excluded"])
        normalize_requirement_row(req, _mapping_for(session, imp, student))
    elif course is not None:
        if changes.get("excluded") is not None:
            course.excluded = bool(changes["excluded"])
    else:
        timp = linked_transcript(session, imp)
        if timp is None or all(r.id != row_id for r in timp.rows):
            raise ImportNotFound("No such row in this import.")
        history = {k: v for k, v in changes.items() if k in (*transcripts.ROW_EDITABLE,
                                                               "excluded", "resolution")}
        transcripts.edit_row(session, account_id, timp.id, row_id, history,
                             expected_version=timp.version, now=now,
                             source_document=DN_AUDIT_SOURCE)
    from app.services.degree_audit.reconciliation import reconcile
    reconcile(session, student, imp, now)
    imp.version += 1
    session.flush()
    return imp


def _mapping_for(session: Session, imp: DegreeAuditImport, student: Student) -> DnMapping | None:
    if imp.mapping_key is None or imp.mapping_version is None:
        return None
    return next((m for m in load_mappings(student.catalog_year)
                 if m.program_key == imp.mapping_key), None)


def discard_import(session: Session, account_id: uuid.UUID, import_id: uuid.UUID,
                   now: datetime) -> DegreeAuditImport:
    student = lock_student(session, account_id)
    if student is None:
        raise ProfileMissing("No academic record exists for this account.")
    imp = get_import(session, student.id, import_id, lock=True)
    if imp.state == ImportState.DISCARDED:
        return imp
    if imp.state == ImportState.CONFIRMED:
        raise ImportRefused("IMPORT_ALREADY_CONFIRMED",
                            "A confirmed import is part of your record; import a newer audit "
                            "to replace it.")
    _clear(session, imp, now)
    session.flush()
    return imp


# --------------------------------------------------------------------------
# confirmation
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfirmResult:
    degree_audit_import: DegreeAuditImport
    already_confirmed: bool


def confirm_import(session: Session, account_id: uuid.UUID, import_id: uuid.UUID, *,
                   expected_version: int, now: datetime) -> ConfirmResult:
    """Confirm one block: its courses through the transcript path, then its
    requirement statuses become the student's progress record. One
    transaction; the caller commits (or rolls everything back)."""
    student = lock_student(session, account_id)
    if student is None:
        raise ProfileMissing("No academic record exists for this account.")
    imp = get_import(session, student.id, import_id, lock=True)
    if imp.state == ImportState.CONFIRMED:
        return ConfirmResult(imp, True)
    state = effective_state(imp, now)
    if state == "expired":
        raise ImportRefused("IMPORT_EXPIRED", "This import expired before it was confirmed; "
                                              "upload the audit again.")
    if state != ImportState.EXTRACTED:
        raise ImportRefused("IMPORT_NOT_CONFIRMABLE", f"This import is {state}.")
    if imp.version != expected_version:
        raise ImportRefused("IMPORT_VERSION_STALE",
                            "This import changed since you reviewed it; review it again.")
    if imp.track_check == "mismatch":
        raise ImportRefused("DN_TRACK_MISMATCH", imp.track_message or
                            "This audit is for a different program than your CoursePilot track.")
    mapping = _mapping_for(session, imp, student)
    for row in imp.requirement_rows:
        normalize_requirement_row(row, mapping)
    blocking = sum(not r.excluded and r.state == DnRowState.NEEDS_REVIEW
                   for r in imp.requirement_rows) + \
        sum(not r.excluded and r.state == "needs_review" for r in imp.course_rows)
    if blocking:
        raise ImportRefused("IMPORT_NOT_READY", "Some rows need review.", [Issue(
            "ROWS_NEED_ATTENTION", None, IssueSeverity.NEEDS_REVIEW,
            f"{blocking} Degree Navigator row(s) need review before confirming.")])

    timp = linked_transcript(session, imp)
    if timp is not None:
        try:
            transcripts.confirm_import(session, account_id, timp.id,
                                       expected_version=timp.version, now=now,
                                       source_document=DN_AUDIT_SOURCE)
        except ImportRefused as exc:
            if exc.keep_changes:
                imp.version += 1          # the student must see the re-validated rows
            raise

    # The newest confirmed audit of a program/Core is the progress record.
    same = select(DegreeAuditImport).where(
        DegreeAuditImport.student_id == student.id, DegreeAuditImport.state == "confirmed",
        DegreeAuditImport.superseded_at.is_(None), DegreeAuditImport.id != imp.id)
    same = same.where(DegreeAuditImport.mapping_key == imp.mapping_key) if imp.mapping_key \
        else same.where(DegreeAuditImport.dn_subject == imp.dn_subject)
    for older in session.scalars(same):
        older.superseded_at = now
    imp.state, imp.confirmed_at = ImportState.CONFIRMED, now
    session.flush()
    from app.services.degree_audit.reconciliation import reconcile
    counts = reconcile(session, student, imp, now, log=True)
    invalidate_audit(session, student.id)
    logger.info("dn_import_confirmed", extra={"import": redact_id(imp.id),
                                              "mapping": imp.mapping_key, "labels": counts})
    return ConfirmResult(imp, False)


__all__ = ["DN_STATUSES_EDITABLE", "EXTRACTOR_VERSION", "FAILURE_MESSAGES", "ConfirmResult",
           "Readiness", "Track", "confirm_import", "discard_import", "get_import",
           "linked_transcript", "list_imports", "normalize_course_row",
           "normalize_requirement_row", "readiness", "resolve_row", "student_track",
           "upload_degree_audit"]
