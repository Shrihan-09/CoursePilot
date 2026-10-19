"""Read-only modeled progress for the authenticated student's own record."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool

from app.api.security import Principal, enforce_request_rate_limit
from app.api.v1.routes.student import _resolve, _unlinked
from app.db.session import get_sync_sessionmaker
from app.domain.progress import ProgressResponse
from app.services.progress import build_progress

router = APIRouter(prefix="/student", tags=["progress"])


def _read(account_id):
    with get_sync_sessionmaker()() as session:
        student = _resolve(session, account_id)
        return None if student is None else build_progress(session, student)


@router.get("/progress", response_model=ProgressResponse)
async def progress(principal: Annotated[Principal, Depends(enforce_request_rate_limit)]):
    if principal.account_id is None:
        raise _unlinked()
    result = await run_in_threadpool(_read, principal.account_id)
    if result is None:
        raise _unlinked()
    return result
