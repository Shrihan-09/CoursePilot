"""Health endpoints.

Two are needed, not one:

  * /health  — liveness. Never touches the database. Used by orchestrators to
               decide whether to restart the process.
  * /ready   — readiness. Checks dependencies. Used to decide whether to send
               traffic. Returns 503 when a dependency is down.

Collapsing them causes restart loops whenever Postgres is briefly unavailable.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str = "coursepilot-backend"
    version: str = "0.1.0"
    environment: str


class ReadyResponse(BaseModel):
    status: Literal["ready", "degraded"]
    database: Literal["ok", "unavailable"]
    pgvector: Literal["ok", "missing", "unknown"]


@router.get("/health", response_model=HealthResponse)
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(status="ok", environment=settings.coursepilot_env.value)


@router.get("/ready", response_model=ReadyResponse)
async def ready(
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> ReadyResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyResponse(status="degraded", database="unavailable", pgvector="unknown")

    # Checked explicitly: a Postgres without pgvector will accept connections
    # happily and then fail at the first vector query, which is a much more
    # confusing failure than reporting it here.
    try:
        result = await session.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        pgvector = "ok" if result.first() else "missing"
    except Exception:
        pgvector = "unknown"

    if pgvector != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyResponse(status="degraded", database="ok", pgvector=pgvector)  # type: ignore[arg-type]

    return ReadyResponse(status="ready", database="ok", pgvector="ok")
