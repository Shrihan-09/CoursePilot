"""Degree Navigator audit import (Phase 7.4; DATA_MODEL section 51).

POST   /api/v1/degree-audit-imports                       body = the PDF bytes
GET    /api/v1/degree-audit-imports
GET    /api/v1/degree-audit-imports/progress              the confirmed record + labels
GET    /api/v1/degree-audit-imports/{import_id}           staged rows, reconciliation
PATCH  /api/v1/degree-audit-imports/{import_id}/rows/{row_id}
POST   /api/v1/degree-audit-imports/{import_id}/confirm   {expected_version}
DELETE /api/v1/degree-audit-imports/{import_id}           discard

Only the authenticated student's own imports exist for them: another
student's id is "not found". CoursePilot never signs in to Degree Navigator;
the student prints their own audit and uploads the PDF.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from app.api.security import Principal, enforce_request_rate_limit, enforce_transcript_upload_limit
from app.api.v1.routes.onboarding import _extraction_error, _read_body, _run, _session
from app.core.config import get_settings
from app.services import onboarding as onboarding_service
from app.services.degree_audit import imports as service
from app.services.degree_audit.overlay import (
    DISAGREE_LABEL,
    ESTIMATED_LABEL,
    active_overlay,
    current_schedule,
    freshness,
    planning_status,
)
from app.services.degree_audit.pdf_worker import default_audit_extractor
from app.services.transcripts.extract import ExtractionFailed, Extractor
from app.services.transcripts.imports import effective_state
from app.services.transcripts.imports import readiness as history_readiness
from app.services.transcripts.storage import TranscriptStorage, get_transcript_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/degree-audit-imports", tags=["degree-audit-imports"])


def get_audit_extractor() -> Extractor:
    """FastAPI dependency (tests substitute it)."""
    return default_audit_extractor()


class Version(BaseModel):
    expected_version: int = Field(ge=1)
    model_config = {"extra": "forbid"}


class RowResolve(Version):
    """Requirement rows: `excluded`, `dn_status`. DN course lines: `excluded`.
    Staged history rows: the transcript review fields and `resolution`."""

    excluded: bool | None = None
    dn_status: str | None = Field(default=None, max_length=32)
    course_code: str | None = Field(default=None, max_length=16)
    supplement_code: str | None = Field(default=None, max_length=4)
    term_code: str | None = Field(default=None, max_length=16)
    status: str | None = Field(default=None, max_length=16)
    grade: str | None = Field(default=None, max_length=8)
    credits_earned: str | int | float | None = None
    credit_origin: str | None = Field(default=None, max_length=16)
    resolution: str | None = Field(default=None, pattern=r"^(keep_existing|replace_existing)$")


def _now() -> datetime:
    return datetime.now(UTC)


def _summary(imp, now) -> dict:
    fresh = freshness(imp, now) if imp.state == "confirmed" or imp.as_of else None
    return {
        "id": str(imp.id), "state": effective_state(imp, now), "version": imp.version,
        "block_index": imp.block_index, "block_count": imp.block_count,
        "dn_subject": imp.dn_subject, "dn_version_label": imp.dn_version_label,
        "as_of": imp.as_of, "as_of_source": imp.as_of_source,
        "freshness": asdict(fresh) if fresh else None,
        "track_check": imp.track_check, "track_message": imp.track_message,
        "mapping_key": imp.mapping_key, "warnings": list(imp.warnings or []),
        "failure_code": imp.failure_code,
        "failure_message": service.FAILURE_MESSAGES.get(imp.failure_code)
        if imp.failure_code else None,
        "created_at": imp.created_at, "expires_at": imp.expires_at,
        "confirmed_at": imp.confirmed_at, "superseded_at": imp.superseded_at,
    }


def _requirement(row, schedule) -> dict:
    planned, rule = planning_status(row, schedule)
    return {
        "id": str(row.id), "kind": row.kind, "row_index": row.row_index,
        "parent_row_index": row.parent_row_index, "dn_number": row.dn_number,
        "dn_heading": row.dn_heading, "dn_code": row.dn_code, "title": row.extracted_title,
        "extracted_icon": row.extracted_icon, "extracted_total": row.extracted_total,
        "extracted_completed": row.extracted_completed, "extracted_needs": row.extracted_needs,
        "dn_courses": row.extracted_courses, "dn_status": row.dn_status,
        "planning_status": planned, "planning_rule": rule,
        "requirement_code": row.requirement_code,
        "mapping_review_status": row.mapping_review_status, "state": row.state,
        "issues": row.issues, "excluded": row.excluded, "edited": row.edited,
        "reconciliation": row.reconciliation, "cp_status": row.cp_status,
        "cp_courses": row.cp_courses,
        "needs_review": row.reconciliation in ("dn_satisfied_cp_not", "cp_satisfied_dn_not",
                                               "allocation_differs"),
        "label": DISAGREE_LABEL if row.reconciliation in (
            "dn_satisfied_cp_not", "cp_satisfied_dn_not", "allocation_differs") else None,
    }


def _course(row) -> dict:
    return {"id": str(row.id), "list_kind": row.list_kind,
            "requirement_number": row.requirement_number, "raw_text": row.raw_text,
            "course_code": row.extracted_course_code, "credits": row.extracted_credits,
            "term": row.extracted_term, "grade": row.extracted_grade, "kind": row.kind,
            "state": row.state, "issues": row.issues, "excluded": row.excluded,
            "history_row_id": str(row.transcript_row_id) if row.transcript_row_id else None}


def _history(row) -> dict:
    return {"id": str(row.id), "course_code": row.course_code, "term_code": row.term_code,
            "status": row.status, "grade": row.grade,
            "credits_earned": str(row.credits_earned) if row.credits_earned is not None else None,
            "state": row.state, "issues": row.issues, "excluded": row.excluded,
            "resolution": row.resolution, "outcome": row.outcome}


def _detail(session, imp, now) -> dict:
    out = _summary(imp, now)
    ready = service.readiness(session, imp, now)
    schedule = current_schedule(session, imp.student_id)
    timp = service.linked_transcript(session, imp)
    out.update({
        "requirements": [_requirement(r, schedule) for r in imp.requirement_rows],
        "courses": [_course(r) for r in imp.course_rows],
        "history": [_history(r) for r in timp.rows] if timp is not None else [],
        "history_version": timp.version if timp is not None else None,
        "history_outcome": ({"inserted": timp.rows_inserted, "replaced": timp.rows_replaced,
                             "skipped": timp.rows_skipped, "excluded": timp.rows_excluded}
                            if timp is not None and timp.state == "confirmed" else None),
        "readiness": asdict(ready),
        "history_readiness": asdict(history_readiness(timp)) if timp is not None else None,
    })
    return out


def _owned_student(session, principal):
    student = onboarding_service.owned_student(session, principal.account_id)
    if student is None:
        raise onboarding_service.ProfileMissing("no profile")
    return student


@router.post("", status_code=201, summary="Upload my Degree Navigator audit (PDF as the body)")
async def upload(request: Request, response: Response,
                 principal: Annotated[Principal, Depends(enforce_transcript_upload_limit)],
                 storage: Annotated[TranscriptStorage, Depends(get_transcript_storage)],
                 extractor: Annotated[Extractor, Depends(get_audit_extractor)]) -> dict:
    settings = get_settings()
    try:
        data = await _read_body(request, settings.transcript_max_bytes)
    except ExtractionFailed as exc:
        logger.info("dn_upload", extra={"principal": principal.redacted(), "outcome": exc.code})
        raise _extraction_error(exc) from None

    def work():
        now = _now()
        with _session() as session:
            imports, created = service.upload_degree_audit(
                session, principal.account_id, data, storage=storage, extractor=extractor,
                now=now, max_bytes=settings.transcript_max_bytes)
            return created, {"imports": [_detail(session, i, now) for i in imports]}
    created, out = await _run(work, "dn_upload", principal)
    if not created:
        response.status_code = status.HTTP_200_OK
    return out


@router.get("", summary="My Degree Navigator imports")
async def list_imports(
        principal: Annotated[Principal, Depends(enforce_request_rate_limit)]) -> dict:
    def work():
        now = _now()
        with _session() as session:
            student = _owned_student(session, principal)
            return {"imports": [_summary(i, now)
                                for i in service.list_imports(session, student.id)]}
    return await _run(work, "dn_list", principal)


@router.get("/progress", summary="Requirement status from my confirmed Degree Navigator audit")
async def progress(principal: Annotated[Principal, Depends(enforce_request_rate_limit)]) -> dict:
    """What planning uses: per mapped node, DN's status (or "estimated" when
    there is none), the rule that decided it and the reconciliation label."""
    def work():
        now = _now()
        with _session() as session:
            student = _owned_student(session, principal)
            overlay = active_overlay(session, student)
            schedule = current_schedule(session, student.id)
            if not overlay.imports:
                return {"source": "coursepilot_estimate", "label": ESTIMATED_LABEL,
                        "imports": [], "requirements": [],
                        "nudge": "Import your Degree Navigator audit for accuracy."}
            imports, rows = [], []
            for imp in overlay.imports:
                imports.append(_summary(imp, now))
                rows += [{**_requirement(r, schedule), "import_id": str(imp.id)}
                         for r in imp.requirement_rows]
            stale = any(i["freshness"] and i["freshness"]["stale"] for i in imports)
            return {"source": "degree_navigator", "label": "From your Degree Navigator audit",
                    "imports": imports, "requirements": rows,
                    "nudge": ("Re-import your Degree Navigator audit: grades have posted or it "
                              "is old.") if stale else None,
                    "unmapped_label": ESTIMATED_LABEL, "disagreement_label": DISAGREE_LABEL}
    return await _run(work, "dn_progress", principal)


@router.get("/{import_id}", summary="One import: staged rows, reconciliation, readiness")
async def read(import_id: uuid.UUID,
               principal: Annotated[Principal, Depends(enforce_request_rate_limit)]) -> dict:
    def work():
        now = _now()
        with _session() as session:
            student = _owned_student(session, principal)
            return _detail(session, service.get_import(session, student.id, import_id), now)
    return await _run(work, "dn_read", principal)


@router.patch("/{import_id}/rows/{row_id}", summary="Resolve one staged row")
async def resolve(import_id: uuid.UUID, row_id: uuid.UUID, body: RowResolve,
                  principal: Annotated[Principal, Depends(enforce_request_rate_limit)]) -> dict:
    def work():
        now = _now()
        with _session() as session:
            changes = body.model_dump(include=body.model_fields_set - {"expected_version"})
            imp = service.resolve_row(session, principal.account_id, import_id, row_id, changes,
                                      expected_version=body.expected_version, now=now)
            session.commit()
            return _detail(session, imp, now)
    return await _run(work, "dn_row_resolve", principal)


@router.post("/{import_id}/confirm",
             summary="Confirm: courses to my history, statuses to my record")
async def confirm(import_id: uuid.UUID, body: Version,
                  principal: Annotated[Principal, Depends(enforce_request_rate_limit)]) -> dict:
    def work():
        now = _now()
        with _session() as session:
            try:
                result = service.confirm_import(session, principal.account_id, import_id,
                                                expected_version=body.expected_version, now=now)
            except service.ImportRefused as exc:
                if exc.keep_changes:
                    session.commit()      # re-validated rows only; nothing was written
                raise
            session.commit()
            return _detail(session, result.degree_audit_import, now)
    return await _run(work, "dn_confirm", principal)


@router.delete("/{import_id}", summary="Discard an open import (its staged rows are deleted)")
async def discard(import_id: uuid.UUID,
                  principal: Annotated[Principal, Depends(enforce_request_rate_limit)]) -> dict:
    def work():
        now = _now()
        with _session() as session:
            imp = service.discard_import(session, principal.account_id, import_id, now)
            session.commit()
            return _summary(imp, now)
    return await _run(work, "dn_discard", principal)
