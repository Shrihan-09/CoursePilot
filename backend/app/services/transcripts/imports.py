"""Transcript import: staging, normalization, review, confirmation (Phase 7.0).

```
upload -> extraction -> normalization -> validation -> STAGING
       -> student review -> explicit confirmation -> student_course
       -> cache invalidation -> engines
```

Never `upload -> parser -> student_course`. The parser's output is evidence;
`transcript_import_row` holds it where no engine reads it, and only
`confirm_import` - called by the student, naming the version of the rows
they reviewed - writes history.

## Normalization

Each extracted row is matched against the course table and validated by
`app.domain.history.validate_entry`, the same rules a typed entry meets.
Its state is DERIVED from its issues, never set by hand:

| state        | meaning                                                   |
|--------------|-----------------------------------------------------------|
| matched      | storable as it stands                                     |
| needs_review | the document did not say (status, term, ...) - the student must |
| unrecognized | no CoursePilot course has this code (incl. transfer/AP with no equivalent) |
| conflict     | disagrees with what is already recorded for that course and term |
| invalid      | cannot be stored as it stands                             |

The ORIGINAL extracted values are kept in their own columns and are never
overwritten; a correction changes only the normalized values.

## Coexistence with recorded history

A staged row for a (course, term) the record already holds:

  * identical values -> matched, confirmation SKIPS it (`ALREADY_RECORDED`);
  * different values -> `conflict` until the student resolves it:
    `keep_existing` (skip) or `replace_existing` (overwrite THAT entry).

Nothing is ever deleted by an import, and nothing recorded is overwritten
without that explicit per-row resolution.

## Confirmation

One transaction: lock the student row, lock the import row, check state,
expiry and version, RE-NORMALIZE every row against the record as it is now
(it may have changed since review), refuse if anything blocks, write,
mark confirmed, invalidate the cached audit, commit. A second confirmation
of a confirmed import returns the stored outcome and writes nothing.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.observability import redact_id
from app.domain.history import Issue, IssueSeverity, parse_credits, validate_entry
from app.domain.provenance import SourceKind
from app.domain.transcript import ExtractedRow, Section, parse_transcript
from app.models import Course, StudentCourse
from app.models.onboarding import (
    ImportState,
    Resolution,
    RowOutcome,
    RowState,
    TranscriptImport,
    TranscriptImportRow,
)
from app.services.history import resolve_course
from app.services.onboarding import ProfileMissing, invalidate_audit, lock_student
from app.services.transcripts.extract import ExtractionFailed, Extractor, check_document
from app.services.transcripts.storage import TranscriptStorage

logger = logging.getLogger(__name__)

#: How long a staged import may wait for review before it can no longer be
#: confirmed. Its rows describe the record as it was; a week is generous.
STAGING_TTL = timedelta(days=7)
#: A crashed request leaves an import `processing`; after this it is failed.
PROCESSING_TIMEOUT = timedelta(minutes=10)
#: Open (processing/extracted) imports per student.
MAX_OPEN_IMPORTS = 3


class TranscriptImportError(Exception):
    code = "import_error"


class ImportNotFound(TranscriptImportError):
    code = "not_found"


class ImportRefused(TranscriptImportError):
    """A structured refusal. `issues` is what the client renders."""

    def __init__(self, code: str, message: str, issues: list[Issue] | None = None,
                 *, keep_changes: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.issues = issues or [Issue(code, None, IssueSeverity.INVALID, message)]
        #: True when the refusal itself produced state the student must see
        #: (re-validated rows): the caller commits instead of rolling back.
        #: Never true when anything was written to the record.
        self.keep_changes = keep_changes


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


def effective_state(imp: TranscriptImport, now: datetime) -> str:
    """The state a client sees: an old `processing` row is a failure, an
    extracted one past its expiry is `expired`. Derived, so no sweeper is
    needed for correctness."""
    if imp.state == ImportState.PROCESSING and _utc(imp.created_at) + PROCESSING_TIMEOUT < now:
        return ImportState.FAILED
    if imp.state == ImportState.EXTRACTED and imp.expires_at is not None \
            and _utc(imp.expires_at) <= now:
        return "expired"
    return imp.state


def _utc(value: datetime) -> datetime:
    from datetime import UTC

    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def open_imports(session: Session, student_id: uuid.UUID, now: datetime) -> list:
    rows = session.scalars(select(TranscriptImport).where(
        TranscriptImport.student_id == student_id,
        TranscriptImport.state.in_((ImportState.PROCESSING, ImportState.EXTRACTED)))
        .order_by(TranscriptImport.created_at))
    return [r for r in rows if effective_state(r, now) in (ImportState.PROCESSING,
                                                           ImportState.EXTRACTED)]


def list_imports(session: Session, student_id: uuid.UUID) -> list[TranscriptImport]:
    return list(session.scalars(select(TranscriptImport).where(
        TranscriptImport.student_id == student_id)
        .order_by(TranscriptImport.created_at.desc()).limit(50)))


def get_import(session: Session, student_id: uuid.UUID, import_id: uuid.UUID,
               *, lock: bool = False) -> TranscriptImport:
    """The owner's import. Another student's is 'not found', never 'forbidden'."""
    q = select(TranscriptImport).where(TranscriptImport.id == import_id,
                                       TranscriptImport.student_id == student_id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    imp = session.scalar(q)
    if imp is None:
        raise ImportNotFound("No such transcript import.")
    return imp


@dataclass(frozen=True, slots=True)
class Readiness:
    ready: bool
    blocking_rows: int
    counts: dict


def readiness(imp: TranscriptImport) -> Readiness:
    counts = dict.fromkeys((*[s for s in (RowState.MATCHED, RowState.NEEDS_REVIEW,
                                          RowState.UNRECOGNIZED, RowState.CONFLICT,
                                          RowState.INVALID)], "excluded"), 0)
    blocking = 0
    for row in imp.rows:
        if row.excluded:
            counts["excluded"] += 1
            continue
        counts[row.state] += 1
        if _row_blocks(row):
            blocking += 1
    return Readiness(ready=imp.state == ImportState.EXTRACTED and blocking == 0,
                     blocking_rows=blocking, counts=counts)


def _row_blocks(row: TranscriptImportRow) -> bool:
    if row.excluded:
        return False
    if row.state == RowState.CONFLICT:
        return row.resolution is None
    return row.state != RowState.MATCHED


# --------------------------------------------------------------------------
# normalization
# --------------------------------------------------------------------------


_EXTERNAL = {Section.TRANSFER: "transfer", Section.EXAM: "exam_credit"}
#: Parse-level codes -> (field, severity, message). NEEDS_REVIEW ones block
#: until the student edits the row (which is them having looked at it).
_PARSE_PROBLEMS = {
    "UNPARSED_TRAILING_TEXT": ("raw_text", IssueSeverity.NEEDS_REVIEW,
                               "The line has text after the credits and grade that could "
                               "not be read."),
    "MULTIPLE_COURSE_CODES": ("course_code", IssueSeverity.NEEDS_REVIEW,
                              "The line holds more than one course record (two columns read "
                              "as one line?); check this row's term and values."),
    "GRADE_COLUMN_UNREADABLE": ("grade", IssueSeverity.NEEDS_REVIEW,
                                "The grade column could not be read."),
    "SECTION_NOT_FOUND": ("raw_text", IssueSeverity.NEEDS_REVIEW,
                          "The section column could not be read; check the row."),
    "TERM_TOTAL_MISMATCH": ("term_code", IssueSeverity.NEEDS_REVIEW,
                            "The credits read under this term do not add up to the term's "
                            "TOTAL CREDITS ATTEMPTED - a row may have been read into the wrong "
                            "term. Check every row of this term."),
    "READING_ORDER_UNRELIABLE": ("term_code", IssueSeverity.NEEDS_REVIEW,
                                 "Parts of this document were read out of order (its columns "
                                 "ran together), so no row's term can be trusted. Check this "
                                 "row's term, credits and grade against your transcript."),
    "COURSE_LINE_UNREADABLE": ("raw_text", IssueSeverity.NEEDS_REVIEW,
                               "A course code was found but the rest of its line could not "
                               "be read. Enter its term, credits and grade, or exclude it."),
    # Informational: never blocks.
    "TERM_NOT_YET_GRADED": ("status", IssueSeverity.INFO,
                            "The transcript shows no average for this term yet; the course "
                            "is probably in progress. Confirm its status."),
}


def _seed_normalized(row: TranscriptImportRow, extracted: ExtractedRow) -> None:
    """Initial normalized values: a MECHANICAL copy of what the document
    said. No status, term or grade is invented when the document is silent."""
    origin = _EXTERNAL.get(extracted.section, "rutgers")
    row.course_code = extracted.course_code
    # A SUP column value ("LB") names the registration record; usually blank.
    row.supplement_code = extracted.supplement
    row.term_code = extracted.term_code
    row.credit_origin = origin
    credits, _ = parse_credits(extracted.credits)
    row.credits_earned = credits
    if origin == "rutgers":
        # A grade on the line means the course was graded: completed. No
        # grade means the document did not say - left for the student.
        row.grade = extracted.grade
        row.status = "completed" if extracted.grade else None
    else:
        # External credit is completed credit; whatever sits in a grade
        # column is not a Rutgers grade and is never carried over.
        row.grade = None
        row.status = "completed"


def normalize_row(session: Session, row: TranscriptImportRow, student_id: uuid.UUID,
                  history: dict) -> None:
    """Recompute a row's course match, issues and state from its NORMALIZED
    values and the current record. Idempotent; called at staging, after every
    edit, and again inside confirmation."""
    issues: list[Issue] = []
    # A line the parser could not fully read needs a person to look at it -
    # until the student has corrected the row, which is that look.
    if not row.edited:
        for code in row.extraction_problems or []:
            fieldname, severity, message = _PARSE_PROBLEMS.get(
                code, (None, IssueSeverity.NEEDS_REVIEW, "Part of this line could not be read."))
            issues.append(Issue(code, fieldname, severity, message))

    entry = validate_entry(term_code=row.term_code, status=row.status, grade=row.grade,
                           credits_earned=row.credits_earned, credit_origin=row.credit_origin,
                           missing_severity=IssueSeverity.NEEDS_REVIEW)
    issues.extend(entry.issues)
    if row.extracted_grade and row.credit_origin in ("transfer", "exam_credit"):
        issues.append(Issue("EXTERNAL_GRADE_NOT_RECORDED", "grade", IssueSeverity.INFO,
                            "The document shows a grade for external credit; Rutgers posts "
                            "no grade for it, so none is recorded."))
    if row.extracted_credit_prefix:
        issues.append(Issue("CREDIT_PREFIX_NOT_RECORDED", "credits_earned", IssueSeverity.INFO,
                            f"The document shows credit prefix {row.extracted_credit_prefix}; "
                            "CoursePilot does not store credit prefixes yet."))
    if row.credits_earned is None and row.extracted_credits is None:
        issues.append(Issue("CREDITS_NOT_EXTRACTED", "credits_earned", IssueSeverity.INFO,
                            "No credits were read; the catalog credits will be used."))

    external = row.credit_origin in ("transfer", "exam_credit")
    match = resolve_course(session, row.course_code, row.supplement_code,
                           unknown_severity=IssueSeverity.NEEDS_REVIEW)
    row.course_id = match.course.id if match.course is not None else None
    unrecognized = False
    if match.issue is not None:
        issue = match.issue
        if issue.code == "COURSE_NOT_FOUND":
            unrecognized = True
            if external:
                issue = Issue("EXTERNAL_CREDIT_NO_EQUIVALENT", "course_code",
                              IssueSeverity.NEEDS_REVIEW,
                              "No CoursePilot course matches this transfer/exam credit. "
                              "Enter its Rutgers equivalent, or exclude the row.")
        issues.append(issue)

    row.existing_record_id = None
    conflict = False
    if row.course_id is not None and entry.term_code is not None and not entry.blocking:
        existing = history.get((row.course_id, entry.term_code))
        if existing is not None:
            row.existing_record_id = existing.id
            if _same(existing, entry, match.course.credits):
                issues.append(Issue("ALREADY_RECORDED", None, IssueSeverity.INFO,
                                    "Already on your record with the same values; "
                                    "confirming leaves it unchanged."))
            else:
                conflict = True
                issues.append(Issue(
                    "CONFLICTS_WITH_RECORD", None, IssueSeverity.CONFLICT,
                    "Your record already has this course in this term with different "
                    "values. Keep the recorded entry, or replace it with this one."))
    if not conflict:
        row.resolution = None

    # Normalized values are stored as validated (grade case, credits scale).
    row.grade, row.status, row.term_code = entry.grade, entry.status, entry.term_code
    row.credit_origin = entry.credit_origin or row.credit_origin

    severities = {i.severity for i in issues}
    if IssueSeverity.INVALID in severities:
        row.state = RowState.INVALID
    elif unrecognized:
        row.state = RowState.UNRECOGNIZED
    elif IssueSeverity.NEEDS_REVIEW in severities:
        row.state = RowState.NEEDS_REVIEW
    elif conflict:
        row.state = RowState.CONFLICT
    else:
        row.state = RowState.MATCHED
    row.issues = [i.as_dict() for i in issues]


def _same(existing: StudentCourse, entry, catalog_credits) -> bool:
    """Equal as the ENGINE reads them: a NULL credits value means the course's
    catalog credits (audit engine and GPA both fall back to it), so "NULL" and
    "3.0" on a 3-credit course are the same entry, not a conflict."""
    def effective(value):
        value = value if value is not None else catalog_credits
        return None if value is None else Decimal(value)

    return (existing.status == entry.status and (existing.grade or None) == entry.grade
            and existing.credit_origin == entry.credit_origin
            and effective(existing.credits_earned) == effective(entry.credits_earned))


def _history(session: Session, student_id: uuid.UUID) -> dict:
    return {(sc.course_id, sc.term_code): sc for sc in session.scalars(
        select(StudentCourse).where(StudentCourse.student_id == student_id))}


def _mark_in_import_duplicates(rows: list[TranscriptImportRow]) -> None:
    """Two included rows for one (course, term) cannot both be written."""
    seen: dict = {}
    for row in rows:
        if row.excluded or row.course_id is None or row.term_code is None:
            continue
        key = (row.course_id, row.term_code)
        if key in seen:
            for dup in (seen[key], row):
                if all(i["code"] != "DUPLICATE_IN_IMPORT" for i in dup.issues):
                    dup.issues = [*dup.issues, Issue(
                        "DUPLICATE_IN_IMPORT", "term_code", IssueSeverity.NEEDS_REVIEW,
                        "This course and term appear more than once in this document; "
                        "exclude one.").as_dict()]
                if dup.state == RowState.MATCHED:
                    dup.state = RowState.NEEDS_REVIEW
        else:
            seen[key] = row


def renormalize(session: Session, imp: TranscriptImport) -> None:
    history = _history(session, imp.student_id)
    for row in imp.rows:
        normalize_row(session, row, imp.student_id, history)
    _mark_in_import_duplicates(list(imp.rows))


# --------------------------------------------------------------------------
# upload
# --------------------------------------------------------------------------


def upload_transcript(session: Session, account_id: uuid.UUID, data: bytes, *,
                      storage: TranscriptStorage, extractor: Extractor, now: datetime,
                      max_bytes: int) -> tuple[TranscriptImport, bool]:
    """(import, created). Re-uploading bytes that are already open returns
    that import; nothing is parsed twice.

    Two commits, on purpose: the `processing` row is durable BEFORE the
    document is read, so a crash mid-extraction leaves a visible, failed
    import (and a storage key a sweep can delete) - never a silent nothing.
    """
    check_document(data, max_bytes)       # size + magic bytes, before any storage
    digest = hashlib.sha256(data).hexdigest()

    student = lock_student(session, account_id)
    if student is None:
        raise ProfileMissing("No academic record exists for this account.")
    existing = session.scalar(select(TranscriptImport).where(
        TranscriptImport.student_id == student.id, TranscriptImport.content_sha256 == digest,
        TranscriptImport.state.in_((ImportState.PROCESSING, ImportState.EXTRACTED))))
    if existing is not None and effective_state(existing, now) in (ImportState.PROCESSING,
                                                                   ImportState.EXTRACTED):
        session.commit()
        return existing, False
    if existing is not None:
        # Stale (crashed / expired): retire it so the same document can be
        # imported afresh.
        _retire(existing, now)
        session.flush()
    if len(open_imports(session, student.id, now)) >= MAX_OPEN_IMPORTS:
        raise ImportRefused("TOO_MANY_OPEN_IMPORTS",
                            "Confirm or discard an open transcript import first.")

    imp = TranscriptImport(student_id=student.id, state=ImportState.PROCESSING,
                           content_sha256=digest, byte_size=len(data), warnings=[])
    session.add(imp)
    try:
        session.flush()
    except IntegrityError:        # pragma: no cover - serialized by the student lock
        session.rollback()
        raise ImportRefused("UPLOAD_IN_PROGRESS",
                            "This document is already being imported.") from None
    imp.storage_key = storage.put(data)
    session.commit()
    import_id, student_id = imp.id, student.id

    failure: str | None = None
    try:
        text = extractor(storage.get(imp.storage_key))
        parsed = parse_transcript(text.pages)
        if "TOO_MANY_ROWS" in parsed.warnings:
            failure = "TOO_MANY_ROWS"
        elif not parsed.rows:
            failure = "NO_COURSE_ROWS"
    except ExtractionFailed as exc:
        failure = exc.code
    except Exception:
        # Never the exception's text: it can quote the document.
        logger.error("transcript_extraction_crashed",
                     extra={"import": redact_id(import_id)})
        failure = "PDF_EXTRACTION_CRASHED"
    finally:
        _drop_document(session, storage, imp)

    imp = session.get(TranscriptImport, import_id)
    if failure is not None:
        imp.state, imp.failure_code = ImportState.FAILED, failure
        session.commit()
        logger.info("transcript_import_failed", extra={"import": redact_id(import_id),
                                                       "outcome": failure})
        return imp, True

    imp.page_count = text.page_count
    imp.parser_version = parsed.parser_version
    imp.transcript_reported_gpa = parsed.reported_cumulative_gpa
    imp.warnings = list(parsed.warnings)
    imp.term_summaries = [_term_summary(t) for t in parsed.terms]
    for extracted in parsed.rows:
        row = TranscriptImportRow(
            import_id=imp.id, row_index=extracted.row_index,
            page_number=extracted.page_number, line_number=extracted.line_number,
            section=extracted.section, raw_text=extracted.raw_text,
            extracted_course_code=extracted.course_code,
            extracted_title=(extracted.title or None) and extracted.title[:255],
            extracted_term=extracted.term_label, extracted_credits=extracted.credits,
            extracted_credit_prefix=extracted.credit_prefix,
            extracted_grade=(extracted.grade or None) and extracted.grade[:8],
            extracted_class_section=(extracted.class_section or None)
            and extracted.class_section[:8],
            extracted_supplement=(extracted.supplement or None) and extracted.supplement[:4],
            excluded=False, edited=False, issues=[], state=RowState.NEEDS_REVIEW,
            extraction_problems=[p for p in [*extracted.problems, *extracted.hints]
                                 if p in _PARSE_PROBLEMS])
        _seed_normalized(row, extracted)
        imp.rows.append(row)
    session.flush()
    history = _history(session, student_id)
    for row in imp.rows:
        normalize_row(session, row, student_id, history)
    _mark_in_import_duplicates(list(imp.rows))
    imp.state = ImportState.EXTRACTED
    imp.extracted_at = now
    imp.expires_at = now + STAGING_TTL
    session.commit()
    logger.info("transcript_import_extracted",
                extra={"import": redact_id(import_id), "rows": len(imp.rows),
                       "pages": imp.page_count})
    return imp, True


def _term_summary(t) -> dict:
    """A term block's own statements, as strings - review evidence only."""
    def s(v):
        return None if v is None else str(v)

    return {"term_code": t.term_code, "term_label": t.term_label,
            "credits_attempted": s(t.credits_attempted), "rows_credit_sum": s(t.rows_credit_sum),
            "consistent": t.consistent, "term_average": s(t.term_average),
            "cumulative_average": s(t.cumulative_average)}


def _retire(imp: TranscriptImport, now: datetime) -> None:
    """A stale open import: crashed processing -> failed; expired -> discarded
    (its staged rows deleted, as a discard would)."""
    if imp.state == ImportState.PROCESSING:
        imp.state, imp.failure_code = ImportState.FAILED, "PROCESSING_INTERRUPTED"
    else:
        imp.rows.clear()
        imp.state, imp.discarded_at = ImportState.DISCARDED, now


def _drop_document(session: Session, storage: TranscriptStorage,
                   imp: TranscriptImport) -> None:
    """The raw document is never retained, whatever happened."""
    key = imp.storage_key
    if key is None:
        return
    try:
        storage.delete(key)
    except Exception:
        logger.warning("transcript_document_delete_failed",
                       extra={"import": redact_id(imp.id)})
        return
    session.execute(update(TranscriptImport).where(TranscriptImport.id == imp.id)
                    .values(storage_key=None).execution_options(synchronize_session="fetch"))
    session.commit()


def sweep_orphaned_documents(session: Session, storage: TranscriptStorage) -> int:
    """Delete stored documents no processing import refers to (a crash
    between store and delete). Returns how many were removed."""
    live = set(session.scalars(select(TranscriptImport.storage_key).where(
        TranscriptImport.storage_key.is_not(None),
        TranscriptImport.state == ImportState.PROCESSING)))
    removed = 0
    for key in storage.stored_keys():
        if key not in live:
            storage.delete(key)
            removed += 1
    return removed


# --------------------------------------------------------------------------
# review
# --------------------------------------------------------------------------


#: What a review edit may change. Extracted values are never editable.
ROW_EDITABLE = ("course_code", "supplement_code", "term_code", "status", "grade",
                "credits_earned", "credit_origin")


def _open_for_review(session: Session, account_id: uuid.UUID, import_id: uuid.UUID,
                     now: datetime):
    student = lock_student(session, account_id)
    if student is None:
        raise ProfileMissing("No academic record exists for this account.")
    imp = get_import(session, student.id, import_id, lock=True)
    state = effective_state(imp, now)
    if state != ImportState.EXTRACTED:
        raise ImportRefused("IMPORT_NOT_EDITABLE",
                            f"This import is {state}; it can no longer be changed.")
    return student, imp


def edit_row(session: Session, account_id: uuid.UUID, import_id: uuid.UUID,
             row_id: uuid.UUID, changes: dict, *, expected_version: int,
             now: datetime) -> TranscriptImport:
    """Correct a staged row's normalized values, exclude/include it, or
    resolve a conflict. Bumps the import version; the row is re-normalized
    (and every row's in-import duplicate check re-run)."""
    _student, imp = _open_for_review(session, account_id, import_id, now)
    if imp.version != expected_version:
        raise ImportRefused("IMPORT_VERSION_STALE",
                            "This import changed since you loaded it; reload and review again.")
    row = next((r for r in imp.rows if r.id == row_id), None)
    if row is None:
        raise ImportNotFound("No such row in this import.")
    values = {k: v for k, v in changes.items() if k in ROW_EDITABLE}
    if values:
        for name, value in values.items():
            if name == "credits_earned":
                parsed, issue = parse_credits(value)
                if issue is not None:
                    raise ImportRefused(issue.code, issue.message, [issue])
                value = parsed
            elif isinstance(value, str):
                value = value.strip() or None
            setattr(row, name, value)
        row.edited = True
    if "excluded" in changes and changes["excluded"] is not None:
        row.excluded = bool(changes["excluded"])
    renormalize(session, imp)
    if "resolution" in changes:
        resolution = changes["resolution"]
        if resolution is not None and row.state != RowState.CONFLICT:
            raise ImportRefused("NO_CONFLICT_TO_RESOLVE",
                                "This row does not conflict with your record.")
        row.resolution = resolution
    imp.version += 1
    session.flush()
    return imp


def discard_import(session: Session, account_id: uuid.UUID, import_id: uuid.UUID,
                   now: datetime) -> TranscriptImport:
    """Throw an open import away. Its staged rows are deleted - they are a
    copy of the student's transcript, and nothing needs them any more."""
    student = lock_student(session, account_id)
    if student is None:
        raise ProfileMissing("No academic record exists for this account.")
    imp = get_import(session, student.id, import_id, lock=True)
    if imp.state == ImportState.DISCARDED:
        return imp
    if imp.state == ImportState.CONFIRMED:
        raise ImportRefused("IMPORT_ALREADY_CONFIRMED",
                            "A confirmed import is part of your record; edit the entries "
                            "instead.")
    imp.rows.clear()
    imp.state = ImportState.DISCARDED
    imp.discarded_at = now
    session.flush()
    return imp


# --------------------------------------------------------------------------
# confirmation
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfirmResult:
    transcript_import: TranscriptImport
    already_confirmed: bool


def confirm_import(session: Session, account_id: uuid.UUID, import_id: uuid.UUID, *,
                   expected_version: int, now: datetime) -> ConfirmResult:
    """Write the reviewed rows to the record. Transactional and idempotent;
    the caller commits (or, on any exception, rolls back everything)."""
    student = lock_student(session, account_id)
    if student is None:
        raise ProfileMissing("No academic record exists for this account.")
    imp = get_import(session, student.id, import_id, lock=True)
    if imp.state == ImportState.CONFIRMED:
        return ConfirmResult(imp, True)
    state = effective_state(imp, now)
    if state == "expired":
        raise ImportRefused("IMPORT_EXPIRED", "This import expired before it was confirmed; "
                                              "upload the transcript again.")
    if state != ImportState.EXTRACTED:
        raise ImportRefused("IMPORT_NOT_CONFIRMABLE", f"This import is {state}.")
    if imp.version != expected_version:
        raise ImportRefused("IMPORT_VERSION_STALE",
                            "This import changed since you reviewed it; review it again.")

    # The record may have changed since review (a manual edit, another
    # import). Every row is judged against the record as it is NOW.
    before = [(r.id, r.state, r.resolution, r.existing_record_id) for r in imp.rows]
    renormalize(session, imp)
    after = [(r.id, r.state, r.resolution, r.existing_record_id) for r in imp.rows]
    blocking = [r for r in imp.rows if _row_blocks(r)]
    if blocking or before != after:
        if before != after:
            imp.version += 1      # the student must see the re-validated rows
        session.flush()
        issues = [Issue("ROWS_NEED_ATTENTION", None, IssueSeverity.NEEDS_REVIEW,
                        f"{len(blocking)} row(s) need review before confirming.")]
        if before != after:
            issues.append(Issue("RECORD_CHANGED_SINCE_REVIEW", None, IssueSeverity.NEEDS_REVIEW,
                                "Your record changed since this import was reviewed; "
                                "the rows were re-checked."))
        raise ImportRefused("IMPORT_NOT_READY", "Some rows need review.", issues,
                            keep_changes=before != after)

    inserted = replaced = skipped = excluded = 0
    for row in imp.rows:
        if row.excluded:
            row.outcome = RowOutcome.EXCLUDED
            excluded += 1
            continue
        existing = (session.get(StudentCourse, row.existing_record_id)
                    if row.existing_record_id else None)
        if existing is not None and (row.resolution == Resolution.KEEP_EXISTING
                                     or row.state == RowState.MATCHED):
            row.outcome, row.student_course_id = RowOutcome.SKIPPED, existing.id
            skipped += 1
            continue
        if existing is not None and row.resolution == Resolution.REPLACE_EXISTING:
            existing.status, existing.grade = row.status, row.grade
            existing.credits_earned, existing.credit_origin = row.credits_earned, \
                row.credit_origin
            existing.source_kind = SourceKind.STUDENT_UPLOADED_TRANSCRIPT.value
            existing.transcript_import_id = imp.id
            row.outcome, row.student_course_id = RowOutcome.REPLACED, existing.id
            replaced += 1
            continue
        record = StudentCourse(student_id=student.id, course_id=row.course_id,
                               term_code=row.term_code, status=row.status, grade=row.grade,
                               credits_earned=row.credits_earned,
                               credit_origin=row.credit_origin,
                               source_kind=SourceKind.STUDENT_UPLOADED_TRANSCRIPT.value,
                               transcript_import_id=imp.id)
        session.add(record)
        session.flush()
        row.outcome, row.student_course_id = RowOutcome.INSERTED, record.id
        inserted += 1

    imp.state = ImportState.CONFIRMED
    imp.confirmed_at = now
    imp.rows_inserted, imp.rows_replaced = inserted, replaced
    imp.rows_skipped, imp.rows_excluded = skipped, excluded
    session.flush()
    invalidate_audit(session, student.id)
    logger.info("transcript_import_confirmed",
                extra={"import": redact_id(imp.id), "inserted": inserted,
                       "replaced": replaced, "skipped": skipped, "excluded": excluded})
    return ConfirmResult(imp, False)


def latest_reported_gpa(session: Session, student_id: uuid.UUID):
    """(gpa, confirmed_at) from the most recently confirmed import, if any."""
    return session.execute(
        select(TranscriptImport.transcript_reported_gpa, TranscriptImport.confirmed_at)
        .where(TranscriptImport.student_id == student_id,
               TranscriptImport.state == ImportState.CONFIRMED,
               TranscriptImport.transcript_reported_gpa.is_not(None))
        .order_by(TranscriptImport.confirmed_at.desc()).limit(1)).first()


def row_course(session: Session, row: TranscriptImportRow) -> Course | None:
    return session.get(Course, row.course_id) if row.course_id else None


def count_rows(session: Session, import_id: uuid.UUID) -> int:
    return session.scalar(select(func.count()).select_from(TranscriptImportRow).where(
        TranscriptImportRow.import_id == import_id)) or 0


__all__ = ["MAX_OPEN_IMPORTS", "PROCESSING_TIMEOUT", "ROW_EDITABLE", "STAGING_TTL",
           "ConfirmResult", "ImportNotFound", "ImportRefused", "Readiness", "confirm_import",
           "discard_import", "edit_row", "effective_state", "get_import", "latest_reported_gpa",
           "list_imports", "normalize_row", "open_imports", "readiness", "renormalize",
           "sweep_orphaned_documents", "upload_transcript"]
