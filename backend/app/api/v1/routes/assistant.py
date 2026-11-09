"""The assistant and the WebReg hand-off for MY account (Prompt 11 Part B).

```
GET    /student/assistant                         status, today's allowance, my history
POST   /student/assistant/messages                one turn; NDJSON stream of events
POST   /student/assistant/actions   {token}       a tap: Choose this / Confirm
DELETE /student/assistant/history                 delete my whole chat history
GET    /student/plans/current/terms/{term}/webreg          "Ready for WebReg"
PUT    /student/plans/current/terms/{term}/sections        choose a section {course, index_number}
DELETE /student/plans/current/terms/{term}/sections/{course}
```

The stream: `{"type": "activity", "label": "Checked your plan"}` lines while
tools run, then one `{"type": "reply", ...}` (or `{"type": "error", ...}`).
The reply text is sent only after the grounding gate passed it - an
unvalidated reply is never streamed token by token.

Every route resolves the signed-in student from the credential; no path or
body carries a student id (a body that tries is a 422).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.security import Principal, enforce_request_rate_limit, get_model_limiter
from app.api.v1.routes.student import _resolve, _unlinked
from app.core.config import get_settings
from app.db.session import get_sync_sessionmaker

logger = logging.getLogger(__name__)
router = APIRouter(tags=["assistant"])

TERM = r"^\d{4}[0179]$"
PATH_COURSE = r"^\d{2}[:-]\d{3}[:-]\d{3}$"


class ChipIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="", max_length=120)
    value: str = Field(min_length=1, max_length=120)
    question: str | None = Field(default=None, max_length=32)


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(default="", max_length=600)
    chip: ChipIn | None = None
    #: Several clarify answers at once: question id -> chip value.
    answers: dict[str, str] | None = Field(default=None, max_length=6)


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=16, max_length=128)


class SectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course: str = Field(pattern=r"^\d{2}:\d{3}:\d{3}$")
    index_number: str = Field(pattern=r"^\d{5}$")


def _owned(session, principal: Principal):
    student = _resolve(session, principal.account_id)
    if student is None:
        raise _unlinked()
    return student


@router.get("/student/assistant", summary="Assistant status, today's allowance and my history")
async def status(principal: Principal = Depends(enforce_request_rate_limit)) -> dict:
    from app.services.assistant import service
    from app.services.assistant.contracts import FOOTER

    settings = get_settings()

    def work():
        with get_sync_sessionmaker()() as session:
            student = _owned(session, principal)
            enabled, reason = service.availability(settings)
            return {"enabled": enabled, "reason": reason,
                    "daily_limit": settings.assistant_daily_message_limit,
                    "remaining_today": service.remaining_today(session, student, settings),
                    "suggestions": [c.model_dump() for c in service.SUGGESTIONS],
                    "history": service.history(session, student), "footer": FOOTER}
    return await run_in_threadpool(work)


@router.post("/student/assistant/messages", summary="Ask the assistant (NDJSON stream)")
async def message(body: MessageIn, principal: Principal = Depends(enforce_request_rate_limit)
                  ) -> StreamingResponse:
    from app.services.assistant import service

    settings = get_settings()
    if not (body.text.strip() or body.chip or body.answers):
        raise HTTPException(status_code=422, detail="Type a message or tap an option.")
    enabled, reason = service.availability(settings)
    if not enabled:
        raise HTTPException(status_code=503, detail=reason)
    if settings.rate_limit_enabled:
        get_model_limiter(settings).check(f"model:{principal.account_id}")

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def run() -> None:
        try:
            with get_sync_sessionmaker()() as session:
                student = _resolve(session, principal.account_id)
                if student is None:
                    emit({"type": "error", "status": 409,
                          "detail": "Link your student record first."})
                    return
                out = asyncio.run(service.handle_message(
                    session, student, settings, text=body.text,
                    chip=body.chip.model_dump() if body.chip else None, answers=body.answers,
                    on_activity=lambda a: emit({"type": "activity", "label": a.label})))
                emit({"type": "reply", **out})
        except service.DailyLimitReached as exc:
            emit({"type": "error", "status": 429, "detail": str(exc)})
        except service.AssistantUnavailable:
            emit({"type": "error", "status": 503,
                  "detail": "The assistant isn't set up on this server yet."})
        except Exception:
            logger.exception("assistant_turn_failed")
            emit({"type": "error", "status": 500, "detail": "Something went wrong. Try again."})
        finally:
            emit({"type": "end"})

    threading.Thread(target=run, name="assistant-turn", daemon=True).start()

    async def events():
        while True:
            event = await queue.get()
            if event["type"] == "end":
                return
            yield json.dumps(event, default=str) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@router.post("/student/assistant/actions", summary="Do what I tapped (Choose this / Confirm)")
async def action(body: ActionIn, principal: Principal = Depends(enforce_request_rate_limit)
                 ) -> dict:
    from app.services.assistant import service
    from app.services.onboarding import lock_student

    def work():
        with get_sync_sessionmaker()() as session:
            student = lock_student(session, principal.account_id)
            if student is None:
                raise _unlinked()
            try:
                return service.execute_action(session, student, body.token)
            except service.InvalidAction as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from None
    try:
        return await run_in_threadpool(work)
    except HTTPException:
        raise
    except Exception:
        logger.exception("assistant_action_failed")
        raise HTTPException(status_code=500, detail="Something went wrong. Try again.") from None


@router.delete("/student/assistant/history", summary="Delete my assistant chat history")
async def delete_history(principal: Principal = Depends(enforce_request_rate_limit)) -> dict:
    from app.services.assistant import service

    def work():
        with get_sync_sessionmaker()() as session:
            student = _owned(session, principal)
            n = service.delete_history(session, student)
            session.commit()
            return {"deleted_messages": n}
    return await run_in_threadpool(work)


def _planned(session, student, term: str) -> list[dict]:
    from app.services.assistant.db_tools import DbAssistantTools

    try:
        plan = DbAssistantTools(session, student).get_plan()
    except LookupError:
        return []
    return next((t["courses"] for t in plan["terms"] if t["term_code"] == term), [])


@router.get("/student/plans/current/terms/{term}/webreg",
            summary='"Ready for WebReg": my chosen sections\' index numbers for a term')
async def webreg(term: str = Path(pattern=TERM),
                 principal: Principal = Depends(enforce_request_rate_limit)) -> dict:
    from app.services.assistant.webreg import webreg_panel

    def work():
        with get_sync_sessionmaker()() as session:
            student = _owned(session, principal)
            return webreg_panel(session, student, term, _planned(session, student, term))
    return await run_in_threadpool(work)


@router.put("/student/plans/current/terms/{term}/sections",
            summary="Choose a section for a course in a term (saved to that term's schedule)")
async def choose(body: SectionIn, term: str = Path(pattern=TERM),
                 principal: Principal = Depends(enforce_request_rate_limit)) -> dict:
    from app.services.assistant.webreg import SectionNotFound, choose_section, webreg_panel

    def work():
        with get_sync_sessionmaker()() as session:
            student = _owned(session, principal)
            try:
                choose_section(session, student, term_code=term, course=body.course,
                               index_number=body.index_number)
            except SectionNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            session.commit()
            return webreg_panel(session, student, term, _planned(session, student, term))
    return await run_in_threadpool(work)


@router.delete("/student/plans/current/terms/{term}/sections/{course}",
               summary="Forget the section I chose for a course")
async def unchoose(term: str = Path(pattern=TERM), course: str = Path(pattern=PATH_COURSE),
                   principal: Principal = Depends(enforce_request_rate_limit)) -> dict:
    from app.services.assistant.webreg import remove_section, webreg_panel

    def work():
        with get_sync_sessionmaker()() as session:
            student = _owned(session, principal)
            remove_section(session, student, term_code=term, course=course.replace("-", ":"))
            session.commit()
            return webreg_panel(session, student, term, _planned(session, student, term))
    return await run_in_threadpool(work)
