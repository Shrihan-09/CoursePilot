"""Self-service academic profile and program selection (Phase 7.0).

## Ownership

An authenticated account creates ITS OWN academic record: a brand-new
`student` row whose `user_id` is the caller's account, set by the server in
the same INSERT. Nothing here looks a record up by any client-supplied
reference, so there is no path by which a request claims an existing record
- linking an existing (registrar-imported) record to an account stays an
administrator action with its own verification (`app.services.accounts`).

The `student.user_id` UNIQUE index is the guard against duplicates, not an
application check: two concurrent first creates both see "no record", both
insert, one wins, and the loser rolls back and returns the winner's row.

## Program selection

A record is bound to exactly one program version and its catalog year, both
NOT NULL, so creating a record IS choosing a program: one call, one
transaction. Selection is validated against `program`/`program_version`
exactly as scenario targets are (`app.services.scenarios.resolve_version`):
an unknown program, an unavailable catalog year (never "the latest one"), a
key that names only variants, or a version with nothing evaluable is
refused. A version still pending human review is ACCEPTED - it may be the
student's real major - and every response that describes it says so.

## Every writer locks the student row

`lock_student` takes `SELECT ... FOR UPDATE` on the owner's `student` row.
Every Phase 7.0 writer - profile/program change, manual history edits,
staged-row review, transcript confirmation - takes it first, so writes to
one record serialize and a confirmation can never interleave with an edit.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.observability import redact_id
from app.models import (
    LINK_ACTION_CREATED,
    ProgramVersion,
    Student,
    StudentCourse,
    StudentLinkEvent,
    UserAccount,
)
from app.models.onboarding import TranscriptImport
from app.services.programs import SupportStatus, version_info

logger = logging.getLogger(__name__)


class OnboardingError(Exception):
    """A refusal a client can act on. `code` is stable; the message is ours."""

    code = "onboarding_error"


class ProfileMissing(OnboardingError):
    code = "profile_missing"


class OpenImportExists(OnboardingError):
    code = "OPEN_TRANSCRIPT_IMPORT"


@dataclass(frozen=True, slots=True)
class SelectionWarning:
    code: str
    severity: str
    message: str

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "message": self.message}


@dataclass(slots=True)
class ProgramSelection:
    program_key: str
    program_name: str
    program_code: str
    school_code: str
    degree_type: str
    variant: str
    catalog_year: str
    lifecycle_state: str
    publication_basis: str | None
    support_status: str
    warnings: list[SelectionWarning] = field(default_factory=list)


def lifecycle_warnings(session: Session, version: ProgramVersion) -> list[SelectionWarning]:
    """What a student must be told about the rules their record is read with.

    Shared by selection and the profile view, so the warning cannot be shown
    at one and forgotten at the other. The Degree Engine states the same
    lifecycle fact on every audit (`program_pending_review`).
    """
    info = version_info(session, version)
    out: list[SelectionWarning] = []
    if info.support_status is SupportStatus.PENDING_REVIEW:
        detail = ("was changed after review and is awaiting re-review"
                  if version.lifecycle_state == "needs_rereview"
                  else "has not yet been verified by a person against the official "
                       "Rutgers prose")
        out.append(SelectionWarning(
            "requirements_pending_review", "warning",
            f"This program's requirement encoding {detail}. Audits and plans for it "
            "are provisional."))
    from app.services.academic_context import composed_requirements
    has_core = any(r.requirement_system == "core" for r in composed_requirements(session, version))
    if version.publication_basis == "legacy_curated":
        out.append(SelectionWarning("legacy_curated_requirements", "warning",
            "This program is published from legacy curation without a recorded human review."))
    if not has_core:
        out.append(SelectionWarning(
            "school_requirements_not_included", "warning",
            "School-wide requirements (such as the SAS Core) are not part of this "
            "program's encoding; the audit evaluates the program's own requirements only."))
    if info.not_evaluable_rule_count:
        out.append(SelectionWarning(
            "rules_not_evaluable", "info",
            f"{info.not_evaluable_rule_count} program rule(s) cannot be evaluated "
            "automatically and are reported as such in the audit."))
    return out


def describe_selection(session: Session, version: ProgramVersion) -> ProgramSelection:
    from app.services.programs import program_key

    program = version.program
    school = program.school
    info = version_info(session, version)
    return ProgramSelection(
        program_key=program_key(school.code, program.code, program.degree_type,
                                program.variant),
        program_name=program.name, program_code=program.code, school_code=school.code,
        degree_type=program.degree_type, variant=program.variant,
        catalog_year=version.catalog_year, lifecycle_state=version.lifecycle_state,
        publication_basis=version.publication_basis,
        support_status=info.support_status.value,
        warnings=lifecycle_warnings(session, version))


def resolve_selection(session: Session, key: str, catalog_year: str) -> ProgramVersion:
    """The version a selection names, or a ScenarioError subclass.

    Delegates to the scenario resolver so a "program key + catalog year"
    means exactly one thing everywhere in CoursePilot.
    """
    from app.services.scenarios import resolve_version

    version, _info = resolve_version(session, key, catalog_year)
    return version


def owned_student(session: Session, account_id: uuid.UUID) -> Student | None:
    return session.scalar(select(Student).where(Student.user_id == account_id))


def lock_student(session: Session, account_id: uuid.UUID) -> Student | None:
    """The account's own student row, locked FOR UPDATE until commit.

    `populate_existing` so the returned object carries the values read under
    the lock, not whatever an earlier read in this session cached.
    """
    return session.scalar(
        select(Student).where(Student.user_id == account_id)
        .with_for_update().execution_options(populate_existing=True))


def invalidate_audit(session: Session, student_id: uuid.UUID) -> None:
    """Drop the cached audit inside the caller's transaction - best effort.

    NOT required for correctness: the audit cache key is a fingerprint of
    the current rows, so a changed record can never be served a stale audit
    (`app.services.audit.cache`). This reclaims the row at once. It runs in a
    SAVEPOINT so a failure here can never roll back the academic write it
    accompanies; it is logged and the write proceeds.
    """
    from app.models import StudentAuditCache

    try:
        with session.begin_nested():
            row = session.get(StudentAuditCache, student_id)
            if row is not None:
                session.delete(row)
                session.flush()
    except Exception:
        logger.warning("onboarding_cache_invalidate_failed", exc_info=True)


def create_profile(session: Session, account_id: uuid.UUID, *, program_key: str,
                   catalog_year: str) -> tuple[Student, bool]:
    """(student, created). Idempotent: an existing record is returned as is.

    A repeated call never re-binds the program - changing a program is an
    explicit academic decision with its own call (`change_program`), not a
    side effect of a retried request.
    """
    existing = owned_student(session, account_id)
    if existing is not None:
        return existing, False
    if session.get(UserAccount, account_id) is None:
        raise ProfileMissing("This account is not provisioned.")

    version = resolve_selection(session, program_key, catalog_year)
    student = Student(user_id=account_id, program_version_id=version.id,
                      catalog_year=version.catalog_year, external_ref=None)
    session.add(student)
    try:
        session.flush()
        session.add(StudentLinkEvent(student_id=student.id, user_account_id=account_id,
                                     performed_by_id=account_id, action=LINK_ACTION_CREATED,
                                     reason="self-service academic profile"))
        session.flush()
    except IntegrityError:
        # The concurrent first create won. Its row is THE record.
        session.rollback()
        winner = owned_student(session, account_id)
        if winner is None:     # pragma: no cover - the constraint says otherwise
            raise
        return winner, False
    logger.info("profile_created", extra={"account": redact_id(account_id)})
    return student, True


def change_program(session: Session, account_id: uuid.UUID, *, program_key: str,
                   catalog_year: str) -> Student:
    """Re-bind the record to another program version. Courses are untouched.

    Program and catalog year move TOGETHER (the engine reports a mismatch
    between them as a blocking data error). The cached audit is invalidated
    in the same transaction; staged transcript rows are unaffected - they
    record courses, which do not depend on the program.
    """
    student = lock_student(session, account_id)
    if student is None:
        raise ProfileMissing("No academic record exists for this account.")
    version = resolve_selection(session, program_key, catalog_year)
    if student.program_version_id != version.id or student.catalog_year != version.catalog_year:
        student.program_version_id = version.id
        student.catalog_year = version.catalog_year
        session.flush()
        invalidate_audit(session, student.id)
        logger.info("program_changed", extra={"account": redact_id(account_id)})
    return student


@dataclass(slots=True)
class OnboardingStatus:
    state: str
    student: Student | None
    counts: dict
    open_imports: list[TranscriptImport]
    completed_at: datetime | None
    next_steps: list[str]


def onboarding_status(session: Session, account_id: uuid.UUID,
                      now: datetime) -> OnboardingStatus:
    """Where the account is in onboarding - derived, never stored, except the
    one explicit fact the student states ("I'm done": `onboarding_completed_at`).

    needs_profile -> needs_academic_history | reviewing_transcript
    -> ready_to_complete -> complete
    """
    student = owned_student(session, account_id)
    if student is None:
        return OnboardingStatus("needs_profile", None, {}, [], None, ["create_profile"])
    counts = dict.fromkeys(("completed", "in_progress", "planned"), 0)
    for status, n in session.execute(
            select(StudentCourse.status, func.count()).where(
                StudentCourse.student_id == student.id).group_by(StudentCourse.status)):
        counts[status] = n
    from app.services.transcripts.imports import open_imports

    pending = open_imports(session, student.id, now)
    if student.onboarding_completed_at is not None:
        state, steps = "complete", []
    elif pending:
        state, steps = "reviewing_transcript", ["review_transcript", "confirm_transcript",
                                                "discard_transcript"]
    elif not any(counts.values()):
        state, steps = "needs_academic_history", ["upload_transcript", "add_courses",
                                                  "complete_onboarding"]
    else:
        state, steps = "ready_to_complete", ["add_courses", "upload_transcript",
                                             "complete_onboarding"]
    return OnboardingStatus(state, student, counts, pending, student.onboarding_completed_at,
                            steps)


def complete_onboarding(session: Session, account_id: uuid.UUID, now: datetime) -> Student:
    """The student says onboarding is done. Idempotent.

    Refused while a transcript import is open: completing would leave staged
    rows the student has neither confirmed nor discarded. An EMPTY history is
    allowed - an incoming first-year has none.
    """
    student = lock_student(session, account_id)
    if student is None:
        raise ProfileMissing("No academic record exists for this account.")
    if student.onboarding_completed_at is not None:
        return student
    from app.services.transcripts.imports import open_imports

    if open_imports(session, student.id, now):
        raise OpenImportExists("Confirm or discard the open transcript import first.")
    student.onboarding_completed_at = now
    session.flush()
    return student


__all__ = ["OnboardingError", "OnboardingStatus", "OpenImportExists", "ProfileMissing",
           "ProgramSelection", "SelectionWarning", "change_program", "complete_onboarding",
           "create_profile", "describe_selection", "invalidate_audit", "lifecycle_warnings",
           "lock_student", "onboarding_status", "owned_student", "resolve_selection"]
