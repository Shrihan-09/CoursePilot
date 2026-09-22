"""Grounded explanation endpoint (Phase 5.2, Parts G-J, O, Q, R).

## The contract, and the shape it deliberately refuses

```
Authorization: Bearer <credential>
POST /api/v1/explanations/recommendation
{ "course_key": "01:198:344", "explanation_type": "..." }
```

The request names a **course**. The STUDENT comes from the authenticated
principal (Phase 5.3), and the decision is re-derived from the database, so a
client can neither name another student nor submit

```json
{"satisfaction": true, "credits": 99}
```

and have it become authoritative. There is no field on the request body that
can reach an academic fact, and there is no free-text `prompt` field that
could reach the model as a question.

The model never sees the HTTP request. It sees evidence the Degree Engine
produced.

## Why the work runs in a thread

The audit engine, the optimizer and the search index are synchronous by
design. Rather than rewrite the deterministic core to suit the transport,
the request does that work in a worker thread with a sync session.

## Failure policy (Part J)

| Condition | Response |
|---|---|
| malformed body / oversized field | 422 (FastAPI validation) |
| authenticated, no linked academic record | 409 |
| course not found | 404 |
| provider unavailable, timeout, or error | 200 with the deterministic explanation |
| model output invalid | 200 with the deterministic explanation |
| Degree Engine failure | 500 - never disguised as a successful explanation |

The last row matters most. A domain failure must not be dressed up as a
confident AI answer; if the audit could not run, there is nothing to explain.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.security import Principal, enforce_request_rate_limit, get_model_limiter
from app.core.config import Settings, get_settings
from app.db.session import get_sync_sessionmaker
from app.models import Course, UserAccount
from app.services.audit.baseline import compute_baseline
from app.services.audit.engine import DegreeAuditEngine
from app.services.explanations import (
    ExplanationType,
    RecommendationExplanationService,
)
from app.services.explanations.providers import build_explanation_model
from app.services.search.bm25 import build_bm25
from app.services.search.documents import build_course_documents
from app.services.search.synonyms import ExpandingSearcher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/explanations", tags=["explanations"])

_COURSE_KEY = r"^\d{2}:\d{3}:\d{3}$"


class RecommendationExplanationRequest(BaseModel):
    """Names a course. Carries no identity and no academic claim.

    `student_ref` was REMOVED in Phase 5.3. The student comes from the
    authenticated principal, so a caller cannot name a student at all - which
    is why Student A cannot request Student B's audit. There is nowhere to
    put "B".

    There is likewise no `provider`, `max_tokens` or `temperature`: model
    bounds are server configuration, not client input.
    """

    course_key: str = Field(pattern=_COURSE_KEY)
    explanation_type: Literal[
        "why_recommended", "how_it_helps", "what_is_course", "why_not_recommended"
    ] = "why_recommended"

    model_config = {"extra": "forbid"}


class ExplanationResponse(BaseModel):
    """Validated explanation data only - never raw provider output."""

    summary: str
    reasons: list[str] = []
    course_information: list[str] = []
    limitations: list[str] = []
    citations: list[str] = []
    #: "deterministic" or "model". Surfaced so a caller can tell whether a
    #: model was involved at all.
    generated_by: str
    grounded: bool
    used_model: bool
    request_id: str


def _build_explanation(
    settings: Settings,
    account_id,
    course_key: str,
    explanation_type: ExplanationType,
):
    """All synchronous work for one request. Runs in a worker thread.

    The student is resolved by OWNERSHIP (`student.user_id == account_id`),
    never by a name the caller supplied. There is no query in this function
    that a client can influence toward another person's record.
    """
    from app.services.accounts import AccountNotLinked, resolve_owned_student

    sessionmaker = get_sync_sessionmaker()
    with sessionmaker() as session:
        account = session.get(UserAccount, account_id)
        if account is None:
            # Authenticated but no account row: from the caller's side this
            # is indistinguishable from owning nothing, and reporting it as
            # an internal error would leak that distinction.
            return None, "unlinked"
        try:
            student = resolve_owned_student(session, account)
        except AccountNotLinked:
            return None, "unlinked"

        course = session.scalar(
            select(Course).where(
                Course.course_string == course_key, Course.supplement_code == ""
            )
        )
        if course is None:
            return None, "course"

        audit = DegreeAuditEngine(session).audit(student)
        baseline = compute_baseline(session, student).satisfied

        documents = build_course_documents(session)
        by_key = {d.course_key: d for d in documents}
        searcher = ExpandingSearcher(build_bm25(documents))

        service = RecommendationExplanationService(
            documents_by_key=by_key,
            searcher=searcher,
            model=build_explanation_model(settings),
        )

        if explanation_type is ExplanationType.WHY_NOT_RECOMMENDED:
            outcome = service.explain_not_recommended(audit, course_key)
        elif explanation_type is ExplanationType.WHAT_IS_COURSE:
            outcome = service.describe_course(course_key)
        else:
            outcome = service.explain_recommendation(
                audit,
                course_key,
                explanation_type=explanation_type,
                baseline_satisfied=baseline,
            )
        return outcome, None


@router.post(
    "/recommendation",
    response_model=ExplanationResponse,
    status_code=status.HTTP_200_OK,
    summary="Explain a decision CoursePilot has already made",
)
async def explain_recommendation(
    payload: RecommendationExplanationRequest,
    principal: Principal = Depends(enforce_request_rate_limit),
    settings: Settings = Depends(get_settings),
) -> ExplanationResponse:
    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()

    # Structured logging carries identifiers and timings, never student data,
    # never prompts, never model responses.
    logger.info(
        "explanation_requested",
        extra={
            "request_id": request_id,
            # A hashed handle, never the NetID: logs correlate requests, they
            # do not identify people.
            "principal": principal.redacted(),
            "course_key": payload.course_key,
            "explanation_type": payload.explanation_type,
            "provider": settings.explanation_provider,
        },
    )

    # Model-call budget, checked BEFORE any work. Separate from the request
    # budget because this one protects spend: a provider call costs tokens
    # and a deterministic explanation does not.
    if settings.rate_limit_enabled and settings.explanation_provider != "none":
        get_model_limiter(settings).check(f"model:{principal.subject}")

    try:
        outcome, missing = await run_in_threadpool(
            _build_explanation,
            settings,
            principal.account_id,
            payload.course_key,
            ExplanationType(payload.explanation_type),
        )
    except Exception:
        # A Degree Engine failure is NOT an explanation. Surfacing it as a
        # 500 keeps a domain error from being dressed up as a confident
        # answer.
        logger.exception("explanation_failed", extra={"request_id": request_id})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to compute a degree audit for this request.",
        ) from None

    if outcome is None:
        logger.info(
            "explanation_unavailable",
            extra={"request_id": request_id, "reason": missing},
        )
        if missing == "unlinked":
            # A distinct, controlled state: authentication succeeded, but no
            # academic record is linked to this account. Reporting it as 404
            # would tell the user their own data is missing; reporting it as
            # 403 would imply they were refused. 409 says the account is not
            # in a state where this request is meaningful yet.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "No academic record is linked to this account. "
                    "Linking requires verification and cannot be self-served."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such recommendation.",
        )

    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "explanation_completed",
        extra={
            "request_id": request_id,
            "used_model": outcome.used_model,
            "grounded": outcome.grounded,
            "rejected": bool(outcome.rejection_problems),
            "latency_ms": round(elapsed_ms, 1),
        },
    )
    if outcome.rejection_problems:
        # Logged so a drifting provider is visible, with the reasons but not
        # the response text.
        logger.warning(
            "model_output_rejected",
            extra={
                "request_id": request_id,
                "problems": list(outcome.rejection_problems),
            },
        )

    explanation = outcome.explanation
    return ExplanationResponse(
        summary=explanation.summary,
        reasons=list(explanation.reasons),
        course_information=list(explanation.course_information),
        limitations=list(explanation.limitations),
        citations=list(explanation.citations),
        generated_by=explanation.generated_by,
        grounded=outcome.grounded,
        used_model=outcome.used_model,
        request_id=request_id,
    )
