"""The signed-in account, as the product shell needs it (Phase 7.1).

```
GET /api/v1/me
```

Authentication only: "who is this CoursePilot user, and what kind of
sign-in is it?". It reports NO identifier - not the account id, not the
provider subject, not an email - because the client never needs one: every
other route resolves the caller's records from the credential itself.

Three trust boundaries stay separate (docs/AUTHENTICATION.md):

  1. authentication           - this route;
  2. CoursePilot academic data - what the student explicitly provided
                                 (/student/profile, /student/courses,
                                 transcript imports); `has_academic_profile`
                                 only says whether one exists;
  3. institutional authorization - official Rutgers integrations. None
                                 exists; nothing here implies one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.api.security import Principal, enforce_request_rate_limit
from app.db.session import get_sync_sessionmaker

router = APIRouter(tags=["account"])


class InstitutionView(BaseModel):
    #: not_configured | verified | not_verified - from SIGNED identity claims.
    #: "verified" means the identity provider asserts membership of a
    #: configured domain. It does NOT mean current enrollment.
    status: str


class MeView(BaseModel):
    #: oidc (a real identity provider) | dev (non-production only)
    sign_in_method: str
    institution: InstitutionView
    has_academic_profile: bool
    onboarding_complete: bool


@router.get("/me", response_model=MeView, summary="The signed-in account (no identifiers)")
async def me(principal: Principal = Depends(enforce_request_rate_limit)) -> MeView:
    def work() -> tuple[bool, bool]:
        from app.services.onboarding import owned_student

        with get_sync_sessionmaker()() as session:
            student = owned_student(session, principal.account_id)
            return student is not None, bool(student and student.onboarding_completed_at)

    has_profile, complete = await run_in_threadpool(work)
    return MeView(sign_in_method=principal.provider,
                  institution=InstitutionView(status=principal.institution),
                  has_academic_profile=has_profile, onboarding_complete=complete)
