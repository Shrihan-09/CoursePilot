"""The authenticated student's own academic context (Phase 5.6).

```
GET /api/v1/student/context     academic FACTS
GET /api/v1/student/audit       the Degree Engine's INTERPRETATION
```

## The client never selects the student

Both routes are singular and parameterless. There is no `{student_id}` in
the path, no `?student_ref=`, no request body, and no field anywhere that
names a person. The student is resolved by **ownership**:

```
principal.account_id -> UserAccount -> Student.user_id == account.id
```

This is the Phase 5.3 property carried forward: Student A cannot read
Student B because there is nowhere to put "B". The absence of the field is
the control - stronger than a check someone can forget to write, and it
cannot be bypassed by a parameter nobody thought to validate, because there
is no parameter at all.

`Student.external_ref` appears in neither response. Phase 5.5 established it
is an ingestion label rather than an identity proof; publishing it would
hand clients a name and an incentive to start passing it back.

## Why two endpoints and not one

Measured on the development database before deciding:

| | cost | payload |
|---|---|---|
| context (facts) | ~2 ms | small |
| audit (interpretation) | ~33 ms | ~24 KB |

Bundling would make every read of a course list pay a 16x cost and carry the
full requirement tree with it. They also change at different rates - facts
change when a record is edited, interpretation changes when the *rules* are
recurated - so they cache and invalidate differently.

The separation is a boundary, not just a cost decision: facts are recorded,
interpretation is computed, and a client that wants to know whether a
requirement is satisfied must ask the engine rather than add up the context
itself.

## This route computes nothing academic

It calls `build_student_context` for facts and `DegreeAuditEngine.audit` for
interpretation, and does no requirement reasoning of its own. No LLM is
involved in either: the model explains decisions, it never makes them.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.api.security import Principal, enforce_request_rate_limit
from app.db.session import get_sync_sessionmaker
from app.domain.audit import DegreeAuditResult
from app.models import UserAccount
from app.services.student_context import (
    ProgramVersionMissing,
    StudentContext,
    build_student_context,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/student", tags=["student"])


# --------------------------------------------------------------------------
# response contract
# --------------------------------------------------------------------------
#
# A deliberate public contract, not ORM serialization. Returning entities
# directly would publish every column added later by accident - which is how
# `user_id` or `external_ref` ends up in an API nobody meant to widen.


class CourseRecordResponse(BaseModel):
    course_string: str
    supplement_code: str
    title: str | None
    term_code: str
    grade: str | None
    credits_earned: Decimal | None
    catalog_credits: Decimal | None
    source_kind: str


class ProgramResponse(BaseModel):
    program_name: str
    program_code: str
    degree_type: str
    school_code: str | None
    school_name: str | None
    catalog_year: str
    program_version_catalog_year: str
    total_credits_min: Decimal | None
    total_credits_max: Decimal | None
    curation_status: str
    source_url: str | None


class AcademicRecordResponse(BaseModel):
    """Three lists, never merged.

    The engine treats these differently and so must the client: completed
    satisfies, in-progress satisfies only provisionally, and planned
    satisfies nothing at all.
    """

    completed: list[CourseRecordResponse]
    in_progress: list[CourseRecordResponse]
    planned: list[CourseRecordResponse]


class StudentContextResponse(BaseModel):
    """What the authenticated student's record contains.

    Deliberately absent:

      * `Student.id`, `Student.user_id`, `UserAccount.id` - a client that
        never receives an identifier cannot replay one, and nothing here
        needs one, because the account already selects the record;
      * `Student.external_ref` - a label, not an identity (Phase 5.5);
      * the identity-provider subject, JWT claims and `is_admin` - security
        metadata, no business in an academic response;
      * linking history - `student_link_event` records who was authorized to
        read this record, which is exactly the sort of thing a read of the
        record should not hand out;
      * credit TOTALS - see `app/services/student_context.py`; a total that
        looks like "credits toward the degree" but is not would be worse than
        no total. Ask the audit.
    """

    program: ProgramResponse
    academic_record: AcademicRecordResponse


def _unlinked() -> HTTPException:
    """The established Phase 5.4/5.5 semantics, unchanged.

    Not 404 - that would tell a student their own record is missing. Not 403
    - that would imply they were refused. 409 says the account is real and
    simply not yet in a state where this request is meaningful.
    """
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "No academic record is linked to this account. "
            "Linking requires verification and cannot be self-served."
        ),
    )


def _resolve(session, account_id):
    """principal -> account -> owned student. The only selector there is."""
    from app.services.accounts import AccountNotLinked, resolve_owned_student

    account = session.get(UserAccount, account_id)
    if account is None:
        # Authenticated against a verifier, but no local account row. From
        # the caller's side this is indistinguishable from owning nothing,
        # and distinguishing it would leak that an account was deleted.
        return None
    try:
        return resolve_owned_student(session, account)
    except AccountNotLinked:
        return None


def _load_context(account_id) -> StudentContext | None:
    with get_sync_sessionmaker()() as session:
        student = _resolve(session, account_id)
        if student is None:
            return None
        return build_student_context(session, student)


def _run_audit(account_id) -> DegreeAuditResult | None:
    from app.services.audit.engine import DegreeAuditEngine

    with get_sync_sessionmaker()() as session:
        student = _resolve(session, account_id)
        if student is None:
            return None
        # The engine's own result, unmodified. The route does not re-derive,
        # post-process, filter or "helpfully" summarise it - a second opinion
        # about academic correctness is exactly what must not exist.
        return DegreeAuditEngine(session).audit(student)


def _to_response(context: StudentContext) -> StudentContextResponse:
    def course(record) -> CourseRecordResponse:
        return CourseRecordResponse(
            course_string=record.course_string,
            supplement_code=record.supplement_code,
            title=record.title,
            term_code=record.term_code,
            grade=record.grade,
            credits_earned=record.credits_earned,
            catalog_credits=record.catalog_credits,
            source_kind=record.source_kind,
        )

    program = context.program
    return StudentContextResponse(
        program=ProgramResponse(
            program_name=program.program_name,
            program_code=program.program_code,
            degree_type=program.degree_type,
            school_code=program.school_code,
            school_name=program.school_name,
            catalog_year=program.catalog_year,
            program_version_catalog_year=program.program_version_catalog_year,
            total_credits_min=program.total_credits_min,
            total_credits_max=program.total_credits_max,
            curation_status=program.curation_status,
            source_url=program.source_url,
        ),
        academic_record=AcademicRecordResponse(
            completed=[course(r) for r in context.completed],
            in_progress=[course(r) for r in context.in_progress],
            planned=[course(r) for r in context.planned],
        ),
    )


@router.get(
    "/context",
    response_model=StudentContextResponse,
    summary="The authenticated student's own academic record",
)
async def student_context(
    principal: Principal = Depends(enforce_request_rate_limit),
) -> StudentContextResponse:
    started = time.perf_counter()
    try:
        context = await run_in_threadpool(_load_context, principal.account_id)
    except ProgramVersionMissing:
        # The record exists and is theirs; the catalog data behind it is
        # broken. That is ours to fix, not theirs to be told they are missing.
        logger.exception("student_context_program_version_missing")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to load the academic context for this account.",
        ) from None

    if context is None:
        raise _unlinked()

    # Counts and timing only. Never course codes, grades, titles or terms -
    # a log line is not a place to accumulate somebody's transcript - and the
    # principal is the hashed handle, never the provider subject.
    logger.info(
        "student_context_served",
        extra={
            "principal": principal.redacted(),
            "completed": len(context.completed),
            "in_progress": len(context.in_progress),
            "planned": len(context.planned),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    )
    return _to_response(context)


@router.get(
    "/audit",
    response_model=DegreeAuditResult,
    summary="The deterministic degree audit for the authenticated student",
)
async def student_audit(
    principal: Principal = Depends(enforce_request_rate_limit),
) -> DegreeAuditResult:
    started = time.perf_counter()
    try:
        result = await run_in_threadpool(_run_audit, principal.account_id)
    except Exception:
        # A Degree Engine failure is never dressed up as a successful audit.
        # An empty or partial result would read as "you have satisfied
        # nothing", which is a false academic statement.
        logger.exception("student_audit_failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to compute a degree audit for this account.",
        ) from None

    if result is None:
        raise _unlinked()

    logger.info(
        "student_audit_served",
        extra={
            "principal": principal.redacted(),
            "status": result.status.value,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    )
    return result
