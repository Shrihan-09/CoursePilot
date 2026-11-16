"""Capability endpoint.

Lets the frontend discover which features are actually implemented, so the UI
can render honest "not available yet" states instead of shipping buttons that
call endpoints returning 501. This list shrinks as features land.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.registration_windows import first_window, registration_schedule

router = APIRouter(tags=["meta"])


class Capabilities(BaseModel):
    """`False` means "not built yet", not "disabled by config"."""

    course_search: bool = False
    #: Phase 5.6: GET /api/v1/student/audit exposes the deterministic engine
    #: to an authenticated, linked student. True because the endpoint exists
    #: and works, not because the engine does - it has worked since Phase 3,
    #: and a capability flag describes what a client can actually call.
    degree_audit: bool = True
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


class RegistrationTier(BaseModel):
    min_degree_credits: int
    label: str
    opens_at: datetime


class RegistrationScheduleView(BaseModel):
    """A term's published registration schedule (degree-credit tiers), as
    bundled in app/core/data with its source. Static, cited data - no student
    information; the client picks the tier from the student's own credits."""

    term_code: str
    term_label: str
    tiers: list[RegistrationTier]
    all_students_opens_at: datetime
    #: When the configured registration window closes (None: not configured).
    closes_at: datetime | None
    nightly_closure: dict[str, str]
    source_url: str
    source_basis: str


@router.get("/registration-schedules/{term_code}", response_model=RegistrationScheduleView,
            summary="A term's registration schedule (degree-credit tiers)")
async def get_registration_schedule(term_code: str) -> RegistrationScheduleView:
    schedule = registration_schedule(term_code)
    if schedule is None:
        raise HTTPException(status_code=404, detail="No registration schedule on file for that term.")
    window = first_window(term_code)
    return RegistrationScheduleView(
        term_code=schedule.term_code, term_label=schedule.term_label,
        tiers=[RegistrationTier(min_degree_credits=t.min_degree_credits, label=t.label,
                                opens_at=t.opens_at) for t in schedule.tiers],
        all_students_opens_at=schedule.all_students_opens_at,
        closes_at=window.closes_at if window else None,
        nightly_closure={"start": schedule.closure_start.strftime("%H:%M"),
                         "end": schedule.closure_end.strftime("%H:%M")},
        source_url=schedule.source_url, source_basis=schedule.source_basis)
