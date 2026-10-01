"""Program discovery (Phase 6.0).

```
GET /api/v1/programs                         every program CoursePilot knows, with versions
GET /api/v1/programs?include_discovered=true  ... plus catalog entries not yet curated
GET /api/v1/programs/{program_key}           one program, e.g. /programs/sas-640-ba-option-a
```

Three kinds of answer, never mixed (Phase 6.3):

  * `programs[].versions[].support_status == "supported"` - published;
  * `... == "pending_review"` - curated but not (or no longer) approved by a
    named human; evaluable, and scenario results say so;
  * `discovered[]` - a catalog page or credential heading with NO curated
    requirements. Returned only when asked for, in its own list, so a
    student-facing program picker cannot be polluted by it. Never auditable.

Read-only catalog facts, identical for every caller - nothing here depends on
who is asking. Authenticated anyway, like every non-health route: the request
budget is per principal, and program discovery is part of the student product
rather than a public API.

Identifiers are natural keys (`sas-640-ba` + catalog year). No UUID is
returned: surrogate keys change on re-ingest, and a mobile client that stored
one would silently point at nothing. `support_status` is derived from curation
provenance by `app.services.programs` so that no client ever has to decide for
itself whether a program is trustworthy.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.api.security import Principal, enforce_request_rate_limit
from app.db.session import get_sync_sessionmaker
from app.services.programs import (
    InvalidProgramKey,
    DiscoveredEntry,
    ProgramInfo,
    get_program,
    list_discovered,
    list_programs,
)

router = APIRouter(prefix="/programs", tags=["programs"])

#: `<school>-<program>-<degree>[-<variant>]`, lower-case alphanumerics.
#: Validated here so a malformed key is a 422 before any database work.
PROGRAM_KEY_PATTERN = r"^[a-z0-9]+-[a-z0-9]+-[a-z0-9]+(-[a-z0-9]+)*$"
PROGRAM_KEY_MAX_LENGTH = 96


class ProgramVersionResponse(BaseModel):
    catalog_year: str
    support_status: str
    lifecycle_state: str
    publication_basis: str | None
    curation_status: str
    requirement_count: int
    rule_count: int
    not_evaluable_rule_count: int
    total_credits_min: Decimal | None
    total_credits_max: Decimal | None
    sharing_policy: str
    source_url: str | None
    curated_by: str | None
    source_prose_sha256: str | None
    last_checked_at: datetime | None


class ProgramDetailResponse(BaseModel):
    program_key: str
    school_code: str
    school_name: str
    program_code: str
    name: str
    degree_type: str
    variant: str
    versions: list[ProgramVersionResponse]


class DiscoveredEntryResponse(BaseModel):
    catalog_year: str
    school_slug: str
    page_title: str
    url_path: str
    source_page_id: str | None
    lifecycle: str
    candidate_key: str | None
    heading: str | None
    credential_type: str | None


class ProgramListResponse(BaseModel):
    programs: list[ProgramDetailResponse]
    #: Present only with `include_discovered=true`.
    discovered: list[DiscoveredEntryResponse] | None = None


def _to_response(info: ProgramInfo) -> ProgramDetailResponse:
    return ProgramDetailResponse(
        program_key=info.program_key,
        school_code=info.school_code,
        school_name=info.school_name,
        program_code=info.program_code,
        name=info.name,
        degree_type=info.degree_type,
        variant=info.variant,
        versions=[
            ProgramVersionResponse(
                catalog_year=v.catalog_year,
                support_status=v.support_status.value,
                lifecycle_state=v.lifecycle_state,
                publication_basis=v.publication_basis,
                curation_status=v.curation_status,
                requirement_count=v.requirement_count,
                rule_count=v.rule_count,
                not_evaluable_rule_count=v.not_evaluable_rule_count,
                total_credits_min=v.total_credits_min,
                total_credits_max=v.total_credits_max,
                sharing_policy=v.sharing_policy,
                source_url=v.source_url,
                curated_by=v.curated_by,
                source_prose_sha256=v.source_prose_sha256,
                last_checked_at=v.last_checked_at,
            )
            for v in info.versions
        ],
    )


def _list(include_discovered: bool, catalog_year: str | None
          ) -> tuple[list[ProgramInfo], list[DiscoveredEntry] | None]:
    with get_sync_sessionmaker()() as session:
        discovered = list_discovered(session, catalog_year) if include_discovered else None
        return list_programs(session), discovered


def _get(key: str) -> ProgramInfo | None:
    with get_sync_sessionmaker()() as session:
        return get_program(session, key)


@router.get("", response_model=ProgramListResponse, summary="Programs CoursePilot can describe")
async def programs(
    include_discovered: bool = Query(
        False, description="Also list catalog entries with no curated requirements."),
    catalog_year: str | None = Query(
        None, pattern=r"^\d{4}-\d{4}$", description="Filter discovered entries."),
    principal: Principal = Depends(enforce_request_rate_limit),
) -> ProgramListResponse:
    infos, discovered = await run_in_threadpool(_list, include_discovered, catalog_year)
    return ProgramListResponse(
        programs=[_to_response(i) for i in infos],
        discovered=None if discovered is None else [
            DiscoveredEntryResponse(**vars_of(d)) for d in discovered],
    )


def vars_of(entry: DiscoveredEntry) -> dict:
    return {f: getattr(entry, f) for f in DiscoveredEntryResponse.model_fields}


@router.get(
    "/{program_key}",
    response_model=ProgramDetailResponse,
    summary="One program and its catalog versions",
)
async def program(
    program_key: str = Path(pattern=PROGRAM_KEY_PATTERN, max_length=PROGRAM_KEY_MAX_LENGTH),
    principal: Principal = Depends(enforce_request_rate_limit),
) -> ProgramDetailResponse:
    try:
        info = await run_in_threadpool(_get, program_key)
    except InvalidProgramKey:
        raise HTTPException(status_code=422, detail="Invalid program key.") from None
    if info is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such program.")
    return _to_response(info)
