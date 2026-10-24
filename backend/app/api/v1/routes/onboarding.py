"""Student onboarding: profile, program, academic history, transcript import (Phase 7.0).

```
GET    /api/v1/student/onboarding                     where am I? (200 even with no profile)
POST   /api/v1/student/onboarding/complete            "I'm done" (idempotent)
POST   /api/v1/student/profile                        {program_key, catalog_year} -> 201 / 200
GET    /api/v1/student/profile
PUT    /api/v1/student/program                        {program_key, catalog_year}
PUT    /api/v1/student/placement                      {math_placement: "01:640:151" | null}
GET    /api/v1/student/courses
POST   /api/v1/student/courses                        one entry
PATCH  /api/v1/student/courses/{entry_id}
DELETE /api/v1/student/courses/{entry_id}
POST   /api/v1/student/transcripts                    body = the PDF bytes (application/pdf)
GET    /api/v1/student/transcripts
GET    /api/v1/student/transcripts/{import_id}        staged rows, issues, readiness
PATCH  /api/v1/student/transcripts/{import_id}/rows/{row_id}
POST   /api/v1/student/transcripts/{import_id}/confirm {expected_version}
DELETE /api/v1/student/transcripts/{import_id}        discard
```

Program choices come from the existing `GET /api/v1/programs` (support
status and lifecycle per catalog year); nothing here hard-codes a program.

## Ownership

Every route resolves the caller's record from the credential - no body,
path or query field names a student or an account (`extra="forbid"`
makes `student_id`/`account_id`/`user_id` a 422). Entry, import and row ids
in paths are looked up WITHIN the caller's record, so another student's id
is a 404, byte-identical to an id that never existed.

## Errors

Taxonomy as everywhere: 404 not_found, 409 unlinked_account (no profile
yet - create one), 422 validation_error. Refusals a student can act on carry
structured issues in `error.issues`:

    {"code": "GRADE_UNRECOGNIZED", "field": "grade", "severity": "invalid",
     "message": "..."}

## Privacy

The upload is the request body - there is no file name to store, log or
echo (file names are often a student's name or RUID). Logs carry the
redacted principal, redacted import handles, counts and outcome codes;
never a course, grade, term, title or any document text.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.middleware import IssueHTTPException
from app.api.security import (
    Principal,
    enforce_request_rate_limit,
    enforce_transcript_upload_limit,
)
from app.core.config import get_settings
from app.db.session import get_sync_sessionmaker
from app.domain.history import Issue, IssueSeverity
from app.models import Course
from app.services import history as history_service
from app.services import onboarding as onboarding_service
from app.services.transcripts import imports as import_service
from app.services.transcripts.extract import ExtractionFailed, Extractor, default_extractor
from app.services.transcripts.storage import TranscriptStorage, get_transcript_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/student", tags=["onboarding"])

_PROGRAM_KEY = r"^[a-z0-9]+(-[a-z0-9]+){2,}$"
_CATALOG_YEAR = r"^\d{4}-\d{4}$"


def _now() -> datetime:
    return datetime.now(UTC)


def get_transcript_extractor() -> Extractor:
    """FastAPI dependency: the configured extractor (tests substitute it)."""
    return default_extractor()


# --------------------------------------------------------------------------
# request contracts
# --------------------------------------------------------------------------


class ProgramChoice(BaseModel):
    program_key: str = Field(max_length=80, pattern=_PROGRAM_KEY)
    catalog_year: str = Field(pattern=_CATALOG_YEAR)

    model_config = {"extra": "forbid"}


# Values arrive as loosely typed strings on purpose: the domain validator
# answers with structured issues ("GRADE_UNRECOGNIZED on grade"), where a
# strict schema would only produce the generic, detail-free 422.
_Credits = str | int | float | None


class EntryCreate(BaseModel):
    course_code: str = Field(max_length=16)
    supplement_code: str | None = Field(default=None, max_length=4)
    term_code: str | None = Field(default=None, max_length=16)
    status: str | None = Field(default=None, max_length=16)
    grade: str | None = Field(default=None, max_length=8)
    credits_earned: _Credits = None
    credit_origin: str | None = Field(default=None, max_length=16)

    model_config = {"extra": "forbid"}


class EntryPatch(BaseModel):
    """Only the fields present are changed; `"grade": null` clears a grade."""

    term_code: str | None = Field(default=None, max_length=16)
    status: str | None = Field(default=None, max_length=16)
    grade: str | None = Field(default=None, max_length=8)
    credits_earned: _Credits = None
    credit_origin: str | None = Field(default=None, max_length=16)

    model_config = {"extra": "forbid"}


class RowPatch(BaseModel):
    expected_version: int = Field(ge=1)
    course_code: str | None = Field(default=None, max_length=16)
    supplement_code: str | None = Field(default=None, max_length=4)
    term_code: str | None = Field(default=None, max_length=16)
    status: str | None = Field(default=None, max_length=16)
    grade: str | None = Field(default=None, max_length=8)
    credits_earned: _Credits = None
    credit_origin: str | None = Field(default=None, max_length=16)
    excluded: bool | None = None
    resolution: Literal["keep_existing", "replace_existing"] | None = None

    model_config = {"extra": "forbid"}


class PlacementChoice(BaseModel):
    """`null` clears it. Validated against the course table by the service."""

    math_placement: str | None = Field(max_length=16)

    model_config = {"extra": "forbid"}


class ConfirmRequest(BaseModel):
    expected_version: int = Field(ge=1)

    model_config = {"extra": "forbid"}


# --------------------------------------------------------------------------
# response contracts
# --------------------------------------------------------------------------


class IssueView(BaseModel):
    code: str
    field: str | None = None
    severity: str
    message: str


class WarningView(BaseModel):
    code: str
    severity: str
    message: str


class ProgramView(BaseModel):
    program_key: str
    program_name: str
    program_code: str
    school_code: str
    degree_type: str
    variant: str
    catalog_year: str
    lifecycle_state: str
    publication_basis: str | None
    #: supported (human-reviewed and published) | pending_review
    support_status: str
    warnings: list[WarningView]


class ReportedGpa(BaseModel):
    """What the student's transcript SAID. Not computed by CoursePilot, never
    overwritten by anything CoursePilot computes."""

    value: Decimal
    source: Literal["student_uploaded_unofficial_transcript"] = \
        "student_uploaded_unofficial_transcript"
    as_of: datetime | None


class ProfileView(BaseModel):
    program: ProgramView
    onboarding_completed_at: datetime | None
    transcript_reported_gpa: ReportedGpa | None
    #: The math course the student's placement places them into, as stated.
    math_placement: str | None = None


class ProfileCreated(BaseModel):
    created: bool
    profile: ProfileView


class ImportSummary(BaseModel):
    import_id: uuid.UUID
    state: str          # processing | extracted | failed | confirmed | discarded | expired
    failure_code: str | None
    created_at: datetime
    expires_at: datetime | None
    confirmed_at: datetime | None
    page_count: int | None
    parser_version: str | None
    #: False until a real Rutgers transcript sample has verified the layout.
    layout_verified: bool = False
    version: int
    transcript_reported_gpa: Decimal | None
    warnings: list[str]
    ready: bool
    blocking_rows: int
    row_counts: dict[str, int]
    rows_inserted: int
    rows_replaced: int
    rows_skipped: int
    rows_excluded: int


class OnboardingView(BaseModel):
    state: str
    profile: ProfileView | None
    history_counts: dict[str, int]
    open_imports: list[ImportSummary]
    next_steps: list[str]


class EntryView(BaseModel):
    entry_id: uuid.UUID
    course_code: str
    supplement_code: str
    title: str | None
    term_code: str
    status: str
    grade: str | None
    credits_earned: Decimal | None
    catalog_credits: Decimal | None
    credit_origin: str
    source_kind: str
    from_transcript_import: bool


class EntryList(BaseModel):
    entries: list[EntryView]


class ExtractedValues(BaseModel):
    raw_text: str
    course_code: str | None
    title: str | None
    term: str | None
    section: str | None            # SEC column
    supplement: str | None         # SUP column (usually blank)
    credits: str | None
    credit_prefix: str | None
    grade: str | None


class TermSummaryView(BaseModel):
    """What one term block of the document STATED, and whether the rows read
    under it add up (`consistent`). Review evidence only - never an input."""

    term_code: str
    term_label: str
    credits_attempted: str | None
    rows_credit_sum: str | None
    consistent: bool | None
    term_average: str | None
    cumulative_average: str | None


class NormalizedValues(BaseModel):
    course_code: str | None
    supplement_code: str | None
    course_title: str | None
    term_code: str | None
    status: str | None
    grade: str | None
    credits_earned: Decimal | None
    credit_origin: str | None


class RowView(BaseModel):
    row_id: uuid.UUID
    row_index: int
    page_number: int
    line_number: int
    section: str
    extracted: ExtractedValues
    normalized: NormalizedValues
    state: str
    issues: list[IssueView]
    excluded: bool
    edited: bool
    resolution: str | None
    conflicts_with_entry_id: uuid.UUID | None
    outcome: str | None
    entry_id: uuid.UUID | None


class ImportDetail(ImportSummary):
    terms: list[TermSummaryView]
    rows: list[RowView]


class ImportList(BaseModel):
    imports: list[ImportSummary]


# --------------------------------------------------------------------------
# mapping
# --------------------------------------------------------------------------


def _program_view(session, student) -> ProgramView:
    from app.models import ProgramVersion

    version = session.get(ProgramVersion, student.program_version_id)
    sel = onboarding_service.describe_selection(session, version)
    return ProgramView(
        program_key=sel.program_key, program_name=sel.program_name,
        program_code=sel.program_code, school_code=sel.school_code,
        degree_type=sel.degree_type, variant=sel.variant, catalog_year=sel.catalog_year,
        lifecycle_state=sel.lifecycle_state, publication_basis=sel.publication_basis,
        support_status=sel.support_status,
        warnings=[WarningView(**w.as_dict()) for w in sel.warnings])


def _profile_view(session, student) -> ProfileView:
    gpa = import_service.latest_reported_gpa(session, student.id)
    return ProfileView(
        program=_program_view(session, student),
        onboarding_completed_at=student.onboarding_completed_at,
        transcript_reported_gpa=ReportedGpa(value=gpa[0], as_of=gpa[1]) if gpa else None,
        math_placement=student.math_placement)


def _entry_view(record, course) -> EntryView:
    return EntryView(
        entry_id=record.id, course_code=course.course_string,
        supplement_code=course.supplement_code or "", title=course.title,
        term_code=record.term_code, status=record.status, grade=record.grade,
        credits_earned=record.credits_earned, catalog_credits=course.credits,
        credit_origin=record.credit_origin, source_kind=record.source_kind,
        from_transcript_import=record.transcript_import_id is not None)


def _summary(imp, now) -> dict:
    ready = import_service.readiness(imp)
    state = import_service.effective_state(imp, now)
    return dict(
        import_id=imp.id, state=state, failure_code=imp.failure_code or (
            "PROCESSING_INTERRUPTED" if state == "failed" and imp.state == "processing"
            else None),
        created_at=imp.created_at, expires_at=imp.expires_at, confirmed_at=imp.confirmed_at,
        page_count=imp.page_count, parser_version=imp.parser_version, version=imp.version,
        transcript_reported_gpa=imp.transcript_reported_gpa, warnings=list(imp.warnings or []),
        ready=ready.ready and state == "extracted", blocking_rows=ready.blocking_rows,
        row_counts=ready.counts, rows_inserted=imp.rows_inserted,
        rows_replaced=imp.rows_replaced, rows_skipped=imp.rows_skipped,
        rows_excluded=imp.rows_excluded)


def _detail(session, imp, now) -> ImportDetail:
    titles = {}
    ids = [r.course_id for r in imp.rows if r.course_id]
    if ids:
        from sqlalchemy import select

        titles = dict(session.execute(select(Course.id, Course.title)
                                      .where(Course.id.in_(ids))).all())
    rows = [RowView(
        row_id=r.id, row_index=r.row_index, page_number=r.page_number,
        line_number=r.line_number, section=r.section,
        extracted=ExtractedValues(
            raw_text=r.raw_text, course_code=r.extracted_course_code,
            title=r.extracted_title, term=r.extracted_term,
            section=r.extracted_class_section, supplement=r.extracted_supplement,
            credits=r.extracted_credits,
            credit_prefix=r.extracted_credit_prefix, grade=r.extracted_grade),
        normalized=NormalizedValues(
            course_code=r.course_code, supplement_code=r.supplement_code,
            course_title=titles.get(r.course_id), term_code=r.term_code, status=r.status,
            grade=r.grade, credits_earned=r.credits_earned, credit_origin=r.credit_origin),
        state=r.state, issues=[IssueView(**i) for i in (r.issues or [])],
        excluded=r.excluded, edited=r.edited, resolution=r.resolution,
        conflicts_with_entry_id=r.existing_record_id, outcome=r.outcome,
        entry_id=r.student_course_id) for r in imp.rows]
    return ImportDetail(**_summary(imp, now),
                        terms=[TermSummaryView(**t) for t in (imp.term_summaries or [])],
                        rows=rows)


# --------------------------------------------------------------------------
# error translation
# --------------------------------------------------------------------------


def _unlinked() -> HTTPException:
    from app.api.v1.routes.student import _unlinked as unlinked

    return unlinked()


def _issues_error(status_code: int, message: str, issues: list[Issue]) -> IssueHTTPException:
    return IssueHTTPException(status_code=status_code, detail=message,
                              issues=[i.as_dict() for i in issues])


def _program_error(exc) -> HTTPException:
    from app.services.programs import InvalidProgramKey
    from app.services.scenarios import (
        CatalogYearUnavailable,
        ProgramChoiceRequired,
        ProgramNotEvaluable,
        ProgramNotFound,
    )

    if isinstance(exc, ProgramNotFound):
        return _issues_error(404, "No such program.", [Issue(
            "PROGRAM_NOT_FOUND", "program_key", IssueSeverity.INVALID,
            "CoursePilot has no program with this key.")])
    if isinstance(exc, CatalogYearUnavailable):
        return _issues_error(404, "No such catalog year for this program.", [Issue(
            "CATALOG_YEAR_UNAVAILABLE", "catalog_year", IssueSeverity.INVALID,
            "Available catalog years: " + (", ".join(exc.available) or "none") + ".")])
    if isinstance(exc, ProgramChoiceRequired):
        return _issues_error(422, "Choose a program variant.", [Issue(
            "PROGRAM_VARIANT_REQUIRED", "program_key", IssueSeverity.INVALID,
            "Choose one of: " + ", ".join(exc.variants) + ".")])
    if isinstance(exc, ProgramNotEvaluable):
        return _issues_error(422, "This program cannot be evaluated.", [Issue(
            "PROGRAM_NOT_EVALUABLE", "program_key", IssueSeverity.INVALID,
            "CoursePilot has no curated requirements it can evaluate for this program "
            "and catalog year.")])
    if isinstance(exc, InvalidProgramKey):
        return _issues_error(422, "Not a program key.", [Issue(
            "PROGRAM_KEY_INVALID", "program_key", IssueSeverity.INVALID,
            "A program key looks like sas-198-ba.")])
    raise exc


async def _run(work, event: str, principal: Principal):
    from app.services.programs import InvalidProgramKey
    from app.services.scenarios import ScenarioError

    try:
        out = await run_in_threadpool(work)
    except onboarding_service.ProfileMissing:
        raise _unlinked() from None
    except (ScenarioError, InvalidProgramKey) as exc:
        logger.info(event, extra={"principal": principal.redacted(), "outcome": "program"})
        raise _program_error(exc) from None
    except history_service.EntryInvalid as exc:
        logger.info(event, extra={"principal": principal.redacted(), "outcome": "invalid"})
        raise _issues_error(422, "The entry cannot be recorded as it is.", exc.issues) from None
    except (history_service.EntryNotFound, import_service.ImportNotFound):
        raise HTTPException(status_code=404, detail="Not found.") from None
    except import_service.ImportRefused as exc:
        logger.info(event, extra={"principal": principal.redacted(), "outcome": exc.code})
        raise _issues_error(422, str(exc), exc.issues) from None
    except onboarding_service.PlacementInvalid as exc:
        raise _issues_error(422, str(exc), [Issue(exc.code, "math_placement",
                                                  IssueSeverity.INVALID, str(exc))]) from None
    except onboarding_service.OpenImportExists as exc:
        raise _issues_error(422, str(exc), [Issue(exc.code, None, IssueSeverity.INVALID,
                                                  str(exc))]) from None
    except ExtractionFailed as exc:
        logger.info(event, extra={"principal": principal.redacted(), "outcome": exc.code})
        raise _extraction_error(exc) from None
    except HTTPException:
        raise
    except Exception:
        # Never the exception text: on these paths it can carry academic data.
        logger.error(f"{event}_failed", extra={"principal": principal.redacted()})
        raise HTTPException(status_code=500, detail="Unable to process the request.") from None
    logger.info(event, extra={"principal": principal.redacted(), "outcome": "ok"})
    return out


_UPLOAD_MESSAGES = {
    "FILE_EMPTY": "The upload was empty.",
    "FILE_TOO_LARGE": "The document is larger than CoursePilot accepts.",
    "FILE_NOT_PDF": "The document is not a PDF.",
}


def _extraction_error(exc: ExtractionFailed) -> IssueHTTPException:
    """A document refused BEFORE it was stored (size, type). Fixed messages."""
    return _issues_error(413 if exc.code == "FILE_TOO_LARGE" else 422,
                         "The document was not accepted.",
                         [Issue(exc.code, "document", IssueSeverity.INVALID,
                                _UPLOAD_MESSAGES.get(exc.code, "The document was not accepted."))])


def _session():
    return get_sync_sessionmaker()()


# --------------------------------------------------------------------------
# onboarding + profile + program
# --------------------------------------------------------------------------


@router.get("/onboarding", response_model=OnboardingView,
            summary="Where the account is in onboarding (200 even before a profile exists)")
async def onboarding(principal: Principal = Depends(enforce_request_rate_limit)
                     ) -> OnboardingView:
    def work():
        now = _now()
        with _session() as session:
            st = onboarding_service.onboarding_status(session, principal.account_id, now)
            return OnboardingView(
                state=st.state,
                profile=_profile_view(session, st.student) if st.student else None,
                history_counts=st.counts,
                open_imports=[ImportSummary(**_summary(i, now)) for i in st.open_imports],
                next_steps=st.next_steps)
    return await _run(work, "onboarding_status", principal)


@router.post("/onboarding/complete", response_model=ProfileView,
             summary="Mark onboarding complete (idempotent)")
async def complete(principal: Principal = Depends(enforce_request_rate_limit)) -> ProfileView:
    def work():
        with _session() as session:
            student = onboarding_service.complete_onboarding(session, principal.account_id,
                                                             _now())
            session.commit()
            return _profile_view(session, student)
    return await _run(work, "onboarding_complete", principal)


@router.post("/profile", response_model=ProfileCreated, status_code=201,
             summary="Create my academic profile with my program and catalog year")
async def create_profile(body: ProgramChoice, response: Response,
                         principal: Principal = Depends(enforce_request_rate_limit)
                         ) -> ProfileCreated:
    def work():
        with _session() as session:
            student, created = onboarding_service.create_profile(
                session, principal.account_id, program_key=body.program_key,
                catalog_year=body.catalog_year)
            session.commit()
            return ProfileCreated(created=created, profile=_profile_view(session, student))
    out = await _run(work, "profile_create", principal)
    if not out.created:
        response.status_code = status.HTTP_200_OK
    return out


@router.get("/profile", response_model=ProfileView, summary="My academic profile")
async def get_profile(principal: Principal = Depends(enforce_request_rate_limit)
                      ) -> ProfileView:
    def work():
        with _session() as session:
            student = onboarding_service.owned_student(session, principal.account_id)
            if student is None:
                raise onboarding_service.ProfileMissing("no profile")
            return _profile_view(session, student)
    return await _run(work, "profile_read", principal)


@router.put("/program", response_model=ProfileView,
            summary="Change my program / catalog year (my courses are untouched)")
async def change_program(body: ProgramChoice,
                         principal: Principal = Depends(enforce_request_rate_limit)
                         ) -> ProfileView:
    def work():
        with _session() as session:
            student = onboarding_service.change_program(
                session, principal.account_id, program_key=body.program_key,
                catalog_year=body.catalog_year)
            session.commit()
            return _profile_view(session, student)
    return await _run(work, "program_change", principal)


@router.put("/placement", response_model=ProfileView,
            summary="State or clear my math placement (the course I am placed into)")
async def set_placement(body: PlacementChoice,
                        principal: Principal = Depends(enforce_request_rate_limit)
                        ) -> ProfileView:
    def work():
        with _session() as session:
            student = onboarding_service.set_math_placement(
                session, principal.account_id, body.math_placement)
            session.commit()
            return _profile_view(session, student)
    return await _run(work, "math_placement_set", principal)


# --------------------------------------------------------------------------
# academic history
# --------------------------------------------------------------------------


@router.get("/courses", response_model=EntryList, summary="My academic history entries")
async def list_courses(principal: Principal = Depends(enforce_request_rate_limit)
                       ) -> EntryList:
    def work():
        with _session() as session:
            student = onboarding_service.owned_student(session, principal.account_id)
            if student is None:
                raise onboarding_service.ProfileMissing("no profile")
            return EntryList(entries=[_entry_view(r, c) for r, c in
                                      history_service.list_entries(session, student)])
    return await _run(work, "history_list", principal)


def _with_course(session, record) -> EntryView:
    return _entry_view(record, session.get(Course, record.course_id))


@router.post("/courses", response_model=EntryView, status_code=201,
             summary="Add one entry to my academic history")
async def add_course(body: EntryCreate,
                     principal: Principal = Depends(enforce_request_rate_limit)) -> EntryView:
    def work():
        with _session() as session:
            record = history_service.add_entry(session, principal.account_id,
                                               **body.model_dump())
            session.commit()
            return _with_course(session, record)
    return await _run(work, "history_add", principal)


@router.patch("/courses/{entry_id}", response_model=EntryView,
              summary="Correct one of my entries")
async def update_course(entry_id: uuid.UUID, body: EntryPatch,
                        principal: Principal = Depends(enforce_request_rate_limit)
                        ) -> EntryView:
    def work():
        with _session() as session:
            record = history_service.update_entry(
                session, principal.account_id, entry_id,
                body.model_dump(include=body.model_fields_set))
            session.commit()
            return _with_course(session, record)
    return await _run(work, "history_update", principal)


@router.delete("/courses/{entry_id}", status_code=204, summary="Remove one of my entries")
async def delete_course(entry_id: uuid.UUID,
                        principal: Principal = Depends(enforce_request_rate_limit)) -> Response:
    def work():
        with _session() as session:
            removed = history_service.delete_entry(session, principal.account_id, entry_id)
            session.commit()
            return removed
    if not await _run(work, "history_remove", principal):
        raise HTTPException(status_code=404, detail="Not found.")
    return Response(status_code=204)


# --------------------------------------------------------------------------
# transcript import
# --------------------------------------------------------------------------


async def _read_body(request: Request, limit: int) -> bytes:
    """The request body, refusing more than `limit` bytes WHILE reading - a
    declared Content-Length is checked first but never trusted alone."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise ExtractionFailed("FILE_TOO_LARGE")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise ExtractionFailed("FILE_TOO_LARGE")
    return bytes(buf)


@router.post("/transcripts", response_model=ImportDetail, status_code=201,
             summary="Upload my Rutgers unofficial transcript (PDF as the request body)")
async def upload_transcript(request: Request, response: Response,
                            principal: Principal = Depends(enforce_transcript_upload_limit),
                            storage: TranscriptStorage = Depends(get_transcript_storage),
                            extractor: Extractor = Depends(get_transcript_extractor)
                            ) -> ImportDetail:
    settings = get_settings()
    try:
        data = await _read_body(request, settings.transcript_max_bytes)
    except ExtractionFailed as exc:
        logger.info("transcript_upload", extra={"principal": principal.redacted(),
                                                "outcome": exc.code})
        raise _extraction_error(exc) from None

    def work():
        now = _now()
        with _session() as session:
            imp, created = import_service.upload_transcript(
                session, principal.account_id, data, storage=storage, extractor=extractor,
                now=now, max_bytes=settings.transcript_max_bytes)
            imp = import_service.get_import(session, imp.student_id, imp.id)
            return created, _detail(session, imp, now)
    created, out = await _run(work, "transcript_upload", principal)
    if not created:
        response.status_code = status.HTTP_200_OK
    return out


@router.get("/transcripts", response_model=ImportList, summary="My transcript imports")
async def list_transcripts(principal: Principal = Depends(enforce_request_rate_limit)
                           ) -> ImportList:
    def work():
        now = _now()
        with _session() as session:
            student = onboarding_service.owned_student(session, principal.account_id)
            if student is None:
                raise onboarding_service.ProfileMissing("no profile")
            return ImportList(imports=[ImportSummary(**_summary(i, now)) for i in
                                       import_service.list_imports(session, student.id)])
    return await _run(work, "transcript_list", principal)


@router.get("/transcripts/{import_id}", response_model=ImportDetail,
            summary="One import: staged rows, issues and readiness")
async def get_transcript(import_id: uuid.UUID,
                         principal: Principal = Depends(enforce_request_rate_limit)
                         ) -> ImportDetail:
    def work():
        now = _now()
        with _session() as session:
            student = onboarding_service.owned_student(session, principal.account_id)
            if student is None:
                raise onboarding_service.ProfileMissing("no profile")
            return _detail(session, import_service.get_import(session, student.id, import_id),
                           now)
    return await _run(work, "transcript_read", principal)


@router.patch("/transcripts/{import_id}/rows/{row_id}", response_model=ImportDetail,
              summary="Correct, exclude or resolve one staged row")
async def edit_row(import_id: uuid.UUID, row_id: uuid.UUID, body: RowPatch,
                   principal: Principal = Depends(enforce_request_rate_limit)) -> ImportDetail:
    def work():
        now = _now()
        with _session() as session:
            changes = body.model_dump(include=body.model_fields_set - {"expected_version"})
            imp = import_service.edit_row(session, principal.account_id, import_id, row_id,
                                          changes, expected_version=body.expected_version,
                                          now=now)
            session.commit()
            return _detail(session, imp, now)
    return await _run(work, "transcript_row_edit", principal)


@router.post("/transcripts/{import_id}/confirm", response_model=ImportDetail,
             summary="Write the reviewed rows to my academic history")
async def confirm(import_id: uuid.UUID, body: ConfirmRequest,
                  principal: Principal = Depends(enforce_request_rate_limit)) -> ImportDetail:
    def work():
        now = _now()
        with _session() as session:
            try:
                result = import_service.confirm_import(
                    session, principal.account_id, import_id,
                    expected_version=body.expected_version, now=now)
            except import_service.ImportRefused as exc:
                if exc.keep_changes:
                    session.commit()      # re-validated rows only; nothing was written
                raise
            session.commit()
            return _detail(session, result.transcript_import, now)
    return await _run(work, "transcript_confirm", principal)


@router.delete("/transcripts/{import_id}", response_model=ImportSummary,
               summary="Discard an open import (its staged rows are deleted)")
async def discard(import_id: uuid.UUID,
                  principal: Principal = Depends(enforce_request_rate_limit)) -> ImportSummary:
    def work():
        now = _now()
        with _session() as session:
            imp = import_service.discard_import(session, principal.account_id, import_id, now)
            session.commit()
            return ImportSummary(**_summary(imp, now))
    return await _run(work, "transcript_discard", principal)
