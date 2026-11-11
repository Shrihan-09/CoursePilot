"""'Report a wrong requirement' (Prompt 08 B1 item 5).

    POST  /requirements/reports            any signed-in account
    GET   /admin/requirement-reports       administrators
    PATCH /admin/requirement-reports/{id}  administrators

A report stores the program, the requirement node and the message. Nothing
about the student is attached - no transcript, plan, grade or student id -
beyond the reporting ACCOUNT id, kept for follow-up and abuse control and
never returned in full (the admin list shows a redacted reference).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.api.security import Principal, enforce_request_rate_limit
from app.api.v1.routes.admin import require_admin_account
from app.core.observability import redact_id
from app.db.session import get_sync_sessionmaker
from app.models import RequirementReport

logger = logging.getLogger(__name__)
router = APIRouter(tags=["requirement reports"])

#: Open reports one account may have at once (abuse bound; never silent).
MAX_OPEN_PER_ACCOUNT = 50


class ReportIn(BaseModel):
    program_key: str = Field(min_length=3, max_length=96, pattern=r"^[a-z0-9-]+$")
    catalog_year: str = Field(pattern=r"^\d{4}-\d{4}$")
    requirement_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_#-]+$")
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be blank")
        return v


class ReportCreated(BaseModel):
    id: uuid.UUID
    status: str


class ReportView(BaseModel):
    id: uuid.UUID
    program_key: str
    catalog_year: str
    requirement_code: str
    requirement_name: str
    message: str
    status: str
    resolved_note: str | None
    created_at: datetime
    reporter: str           # redacted account reference, for grouping only


class ReportUpdate(BaseModel):
    status: str = Field(pattern=r"^(open|resolved|dismissed)$")
    resolved_note: str | None = Field(default=None, max_length=2000)


def _requirement_name(session, key: str, year: str, code: str) -> str | None:
    """The node's name if the program (any version state) composes `code`."""
    from app.services.academic_context import composed_requirements
    from app.services.programs import find_program

    try:
        found = find_program(session, key)
    except Exception:       # noqa: BLE001 - an invalid/variant key is simply not found
        return None
    if found is None:
        return None
    program, _ = found
    for version in program.versions:
        if version.catalog_year != year:
            continue
        for r in composed_requirements(session, version):
            if r.code == code:
                return r.name
    return None


@router.post("/requirements/reports", response_model=ReportCreated,
             status_code=status.HTTP_201_CREATED,
             summary="Report that a requirement looks wrong")
async def create_report(
    body: ReportIn,
    principal: Principal = Depends(enforce_request_rate_limit),
) -> ReportCreated:
    def _work():
        with get_sync_sessionmaker()() as session:
            name = _requirement_name(session, body.program_key, body.catalog_year,
                                     body.requirement_code)
            if name is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND,
                                    "No such requirement in that program and catalog year.")
            open_count = session.scalar(select(func.count()).select_from(RequirementReport).where(
                RequirementReport.account_id == principal.account_id,
                RequirementReport.status == "open"))
            if open_count >= MAX_OPEN_PER_ACCOUNT:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                    "You have many open reports; we'll review those first.")
            report = RequirementReport(account_id=principal.account_id,
                                       program_key=body.program_key,
                                       catalog_year=body.catalog_year,
                                       requirement_code=body.requirement_code,
                                       requirement_name=name, message=body.message)
            session.add(report)
            session.commit()
            logger.info("requirement_report_created",
                        extra={"program_key": body.program_key, "code": body.requirement_code})
            return ReportCreated(id=report.id, status=report.status)

    return await run_in_threadpool(_work)


def _view(r: RequirementReport) -> ReportView:
    return ReportView(id=r.id, program_key=r.program_key, catalog_year=r.catalog_year,
                      requirement_code=r.requirement_code, requirement_name=r.requirement_name,
                      message=r.message, status=r.status, resolved_note=r.resolved_note,
                      created_at=r.created_at, reporter=redact_id(str(r.account_id)))


@router.get("/admin/requirement-reports", response_model=list[ReportView],
            summary="Requirement reports, newest first (administrators)")
async def list_reports(report_status: str | None = Query(default="open", alias="status",
                                                         pattern=r"^(open|resolved|dismissed|all)$"),
                       limit: int = Query(default=200, ge=1, le=500),
                       principal: Principal = Depends(require_admin_account)) -> list[ReportView]:
    def _work():
        with get_sync_sessionmaker()() as session:
            stmt = select(RequirementReport).order_by(RequirementReport.created_at.desc(),
                                                      RequirementReport.id).limit(limit)
            if report_status and report_status != "all":
                stmt = stmt.where(RequirementReport.status == report_status)
            return [_view(r) for r in session.scalars(stmt)]

    return await run_in_threadpool(_work)


@router.patch("/admin/requirement-reports/{report_id}", response_model=ReportView,
              summary="Resolve or dismiss a requirement report (administrators)")
async def update_report(
    report_id: uuid.UUID,
    body: ReportUpdate,
    principal: Principal = Depends(require_admin_account),  # noqa: B008 - FastAPI dependency
) -> ReportView:
    def _work():
        with get_sync_sessionmaker()() as session:
            report = session.get(RequirementReport, report_id)
            if report is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "No such report.")
            report.status = body.status
            report.resolved_note = body.resolved_note
            session.commit()
            return _view(report)

    return await run_in_threadpool(_work)
