"""Capability endpoint.

Lets the frontend discover which features are actually implemented, so the UI
can render honest "not available yet" states instead of shipping buttons that
call endpoints returning 501. This list shrinks as features land.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import Settings, get_settings

router = APIRouter(tags=["meta"])


class Capabilities(BaseModel):
    """`False` means "not built yet", not "disabled by config"."""

    course_search: bool = False
    degree_audit: bool = False
    semester_planning: bool = False
    schedule_generation: bool = False
    registration_likelihood: bool = False
    calendar_sync: bool = False
    discussions: bool = False


class MetaResponse(BaseModel):
    service: str = "coursepilot-backend"
    version: str = "0.1.0"
    environment: str
    llm_provider: str
    retrieval_strategy: str
    capabilities: Capabilities
    # Which Rutgers academic terms we hold verified data for. Empty until
    # ingestion runs against an authoritative source.
    loaded_terms: list[str] = []


@router.get("/meta", response_model=MetaResponse)
async def meta(settings: Settings = Depends(get_settings)) -> MetaResponse:
    return MetaResponse(
        environment=settings.coursepilot_env.value,
        llm_provider=settings.llm_provider,
        retrieval_strategy=settings.retrieval_strategy.value,
        capabilities=Capabilities(),
        loaded_terms=[],
    )
