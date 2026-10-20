"""Owner-scoped staged WebReg PDF imports, no Rutgers authenticated access."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.security import Principal, enforce_request_rate_limit, enforce_transcript_upload_limit
from app.api.v1.routes.onboarding import _read_body
from app.api.v1.routes.student import _unlinked
from app.core.config import get_settings
from app.db.session import get_sync_sessionmaker
from app.domain.webreg import ScheduleParseError
from app.models import Course, CourseOffering, CourseSection, SectionInstructor, SectionMeeting
from app.models.schedule_import import ScheduleImport, StudentRegistration
from app.services.onboarding import lock_student
from app.services.scheduling import imports as service
from app.services.scheduling.meetings import normalize
from app.services.scheduling.modality import classify_meetings
from app.services.scheduling.pdf_worker import default_schedule_extractor
from app.services.transcripts.extract import ExtractionFailed, Extractor

router = APIRouter(prefix="/student", tags=["schedule-imports"])


class Version(BaseModel):
    expected_version: int = Field(ge=1)
    model_config = {"extra": "forbid"}


class RowEdit(Version):
    term_code: str | None = Field(default=None, pattern=r"^\d{4}[0179]$")
    course_string: str | None = Field(default=None, pattern=r"^\d{2}:\d{3}:\d{3}$")
    section_number: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{1,8}$")
    index_number: str | None = Field(default=None, pattern=r"^\d{5}$")
    excluded: bool | None = None
    replace_existing: bool | None = None


def detail(imp):
    expired = imp.state == "extracted" and service.utc(imp.expires_at) <= datetime.now(UTC)
    return {
        "id": str(imp.id),
        "state": "expired" if expired else imp.state,
        "version": imp.version,
        "expires_at": imp.expires_at,
        "parser_version": imp.parser_version,
        "rows": [] if expired else imp.rows,
        "ready": not expired and imp.state == "extracted" and service.ready(imp.rows),
    }


def run(account_id, operation):
    with get_sync_sessionmaker()() as session:
        student = lock_student(session, account_id)
        if student is None:
            raise _unlinked()
        try:
            result = operation(session, student)
            session.commit()
            return result
        except (service.ScheduleImportRefused, ScheduleParseError, ExtractionFailed) as exc:
            session.rollback()
            raise HTTPException(422, detail=str(exc)) from None


def get_owned(session, student, import_id):
    imp = service.owned(session, student.id, import_id, lock=True)
    if imp is None:
        raise HTTPException(404, detail="Schedule import not found.")
    if imp.state == "extracted" and service.utc(imp.expires_at) <= datetime.now(UTC):
        imp.rows, imp.state = [], "expired"
    return imp


@router.post("/schedule-imports", status_code=201)
async def upload(
    request: Request,
    principal: Annotated[Principal, Depends(enforce_transcript_upload_limit)],
    extractor: Annotated[Extractor, Depends(default_schedule_extractor)],
):
    try:
        data = await _read_body(request, get_settings().transcript_max_bytes)
    except ExtractionFailed as exc:
        raise HTTPException(422, detail=exc.code) from None
    return await run_in_threadpool(
        run,
        principal.account_id,
        lambda session, student: detail(
            service.stage(
                session,
                student,
                data,
                extractor=extractor,
                max_bytes=get_settings().transcript_max_bytes,
                now=datetime.now(UTC),
            )
        ),
    )


@router.get("/schedule-imports")
async def list_imports(principal: Annotated[Principal, Depends(enforce_request_rate_limit)]):
    def work(session, student):
        imports = list(
            session.scalars(
                select(ScheduleImport)
                .where(ScheduleImport.student_id == student.id)
                .order_by(ScheduleImport.created_at.desc())
                .limit(50)
            )
        )
        for imp in imports:
            if imp.state == "extracted" and service.utc(imp.expires_at) <= datetime.now(UTC):
                imp.rows, imp.state = [], "expired"
        return {
            "imports": [
                {"id": str(imp.id), "state": imp.state, "expires_at": imp.expires_at}
                for imp in imports
            ]
        }

    return await run_in_threadpool(run, principal.account_id, work)


@router.get("/schedule-imports/{import_id}")
async def read(
    import_id: uuid.UUID, principal: Annotated[Principal, Depends(enforce_request_rate_limit)]
):
    def work(session, student):
        imp = get_owned(session, student, import_id)
        if imp.state == "extracted":
            rows = service.resolve_rows(session, student.id, imp.rows)
            if rows != imp.rows:
                imp.rows = rows
                imp.version += 1
        return detail(imp)

    return await run_in_threadpool(run, principal.account_id, work)


@router.patch("/schedule-imports/{import_id}/rows/{row_id}")
async def edit(
    import_id: uuid.UUID,
    row_id: uuid.UUID,
    body: RowEdit,
    principal: Annotated[Principal, Depends(enforce_request_rate_limit)],
):
    changes = body.model_dump(exclude_unset=True, exclude={"expected_version"})
    if any(
        changes.get(k, "present") is None
        for k in ("term_code", "course_string", "section_number", "excluded", "replace_existing")
    ):
        raise HTTPException(422, detail="Required identity and review fields cannot be null.")
    if changes.get("section_number"):
        changes["section_number"] = changes["section_number"].upper()
    return await run_in_threadpool(
        run,
        principal.account_id,
        lambda session, student: detail(
            service.edit(
                session,
                get_owned(session, student, import_id),
                row_id,
                changes,
                expected_version=body.expected_version,
                now=datetime.now(UTC),
            )
        ),
    )


@router.post("/schedule-imports/{import_id}/confirm")
async def confirm(
    import_id: uuid.UUID,
    body: Version,
    principal: Annotated[Principal, Depends(enforce_request_rate_limit)],
):
    return await run_in_threadpool(
        run,
        principal.account_id,
        lambda session, student: detail(
            service.confirm(
                session,
                get_owned(session, student, import_id),
                expected_version=body.expected_version,
                now=datetime.now(UTC),
            )
        ),
    )


@router.delete("/schedule-imports/{import_id}")
async def discard(
    import_id: uuid.UUID, principal: Annotated[Principal, Depends(enforce_request_rate_limit)]
):
    def work(session, student):
        imp = get_owned(session, student, import_id)
        if imp.state == "confirmed":
            raise service.ScheduleImportRefused("IMPORT_ALREADY_CONFIRMED")
        imp.rows, imp.state = [], "discarded"
        return detail(imp)

    return await run_in_threadpool(run, principal.account_id, work)


@router.get("/registrations")
async def registrations(
    principal: Annotated[Principal, Depends(enforce_request_rate_limit)],
    term: str | None = Query(default=None, pattern=r"^\d{4}[0179]$"),
):
    def work(session, student):
        query = select(StudentRegistration).where(StudentRegistration.student_id == student.id)
        if term:
            query = query.where(StudentRegistration.term_code == term)
        records = list(
            session.scalars(
                query.order_by(
                    StudentRegistration.term_code.desc(),
                    StudentRegistration.course_string,
                    StudentRegistration.section_number,
                ).limit(300)
            )
        )
        ids = [r.section_id for r in records if r.section_id]
        sections = (
            {
                section.id: (section, course)
                for section, course in session.execute(
                    select(CourseSection, Course)
                    .join(CourseOffering, CourseOffering.id == CourseSection.offering_id)
                    .join(Course, Course.id == CourseOffering.course_id)
                    .where(CourseSection.id.in_(ids))
                )
            }
            if ids
            else {}
        )
        valid = set()
        for record in records:
            pair = sections.get(record.section_id)
            if pair is None:
                continue
            section, course = pair
            if (
                section.term_code == record.term_code
                and section.section_number == record.section_number
                and section.index_number == record.index_number
                and course.course_string == record.course_string
                and course.supplement_code == record.supplement_code
            ):
                valid.add(record.id)
        meetings = (
            list(session.scalars(select(SectionMeeting).where(SectionMeeting.section_id.in_(ids))))
            if ids
            else []
        )
        instructors = (
            list(
                session.scalars(
                    select(SectionInstructor).where(SectionInstructor.section_id.in_(ids))
                )
            )
            if ids
            else []
        )
        normalized = {
            r.id: [
                normalize(
                    day=m.meeting_day,
                    start=m.start_time_military,
                    end=m.end_time_military,
                    mode_code=m.meeting_mode_code,
                    mode_desc=m.meeting_mode_desc,
                    campus=m.campus_abbrev,
                    campus_name=m.campus_name,
                    building=m.building_code,
                    room=m.room_number,
                )
                for m in meetings
                if r.id in valid and m.section_id == r.section_id
            ]
            for r in records
        }
        return {
            "registrations": [
                {
                    "id": str(r.id),
                    "term_code": r.term_code,
                    "course_string": r.course_string,
                    "section_number": r.section_number,
                    "supplement_code": r.supplement_code,
                    "index_number": r.index_number,
                    "source": r.source,
                    "confirmed_at": r.confirmed_at,
                    "instructors": [
                        i.name
                        for i in instructors
                        if r.id in valid and i.section_id == r.section_id
                    ],
                    "modality": classify_meetings(
                        normalized[r.id], index=r.index_number or ""
                    ).model_dump(mode="json"),
                    "metadata_state": "available" if r.id in valid else "unavailable",
                    "normalized_meetings": [m.model_dump(mode="json") for m in normalized[r.id]],
                    "meetings": [
                        {
                            "day": m.meeting_day,
                            "start": m.start_time_military,
                            "end": m.end_time_military,
                            "mode": m.meeting_mode_desc,
                            "campus": m.campus_name,
                            "building": m.building_code,
                            "room": m.room_number,
                        }
                        for m in meetings
                        if r.id in valid and m.section_id == r.section_id
                    ],
                }
                for r in records
            ]
        }

    return await run_in_threadpool(run, principal.account_id, work)
