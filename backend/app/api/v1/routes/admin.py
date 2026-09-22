"""Administrative student linking (Phase 5.5).

> Authentication establishes identity. **Linking** establishes which academic
> record that identity is authorized to use. The Degree Engine remains the
> sole authority for academic correctness.

## The workflow

```
admin verifies the person out of band (in person, registrar, ID check)
        |
        v
POST /api/v1/admin/students/{student_id}/link
{ "identity_provider": "oidc", "external_subject": "<their NetID subject>" }
        |
        v
student.user_id = that account, + a StudentLinkEvent, one transaction
```

The admin names the person by the **identity they verified**, not by an
account id. Nothing in the workflow requires listing accounts, so no part of
it can be used to enumerate them.

## Why this is not the Phase 5.3 vulnerability returning

Phase 5.3's flaw was that *naming* a student granted access to it. Here a
student id appears in the path, and it grants nothing:

  * only an authenticated **administrator** reaches this router at all;
  * the admin's authority comes from `user_account.is_admin`, a column no
    request can set;
  * the person being linked never sends any of this.

Identifying a candidate record is not the same as being authorized to use
it, and the authorization comes from the admin, not from the identifier.

## Enumeration

`require_admin` runs **before** any lookup, so a non-admin receives 403
whether or not the student exists. For an administrator - who is trusted to
see the roster anyway - a genuine 404 is the useful answer.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.api.security import Principal, get_link_limiter, get_principal
from app.core.config import Settings, get_settings
from app.db.session import get_sync_sessionmaker
from app.models import Student, UserAccount
from app.services.accounts import (
    AccountNotLinked,
    NotAuthorized,
    StudentAlreadyOwned,
    find_account_by_identity,
    link_student,
    require_admin,
    unlink_student,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class LinkRequest(BaseModel):
    """Names the identity the administrator verified. No account id, no role."""

    identity_provider: str = Field(min_length=1, max_length=32)
    external_subject: str = Field(min_length=1, max_length=255)
    #: Operator note for the audit trail. Bounded so it cannot become a data
    #: dump, and never a place for credentials.
    reason: str | None = Field(default=None, max_length=500)

    model_config = {"extra": "forbid"}


class UnlinkRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)

    model_config = {"extra": "forbid"}


class LinkResponse(BaseModel):
    """Deliberately thin.

    It confirms the operation and nothing else. No account id, no subject, no
    student name, no roster detail - a response is an information channel,
    and an administrative one should not become a convenient way to read
    other people's data back out.
    """

    student_id: uuid.UUID
    linked: bool
    event_recorded: bool


def require_admin_principal(
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_settings),
) -> Principal:
    """Authorize the caller as an administrator, then charge the link budget.

    Runs before any student lookup, so a non-admin cannot learn whether a
    record exists. 403 rather than 404: the caller is authenticated and
    simply lacks authority, and pretending the route does not exist would
    make a real operator's misconfiguration much harder to diagnose.

    `is_admin` is re-read from the database on every request rather than
    carried in the principal or a token claim. Authority that travels inside
    a credential outlives its revocation.
    """
    with get_sync_sessionmaker()() as session:
        account = session.get(UserAccount, principal.account_id)
        if account is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized."
            )
        try:
            require_admin(account)
        except NotAuthorized:
            logger.info("admin_denied", extra={"principal": principal.redacted()})
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized."
            ) from None

    # Charged only to callers who passed authorization, so a rejected
    # non-admin cannot burn an administrator's budget.
    if settings.rate_limit_enabled:
        get_link_limiter(settings).check(f"link:{principal.account_id}")
    return principal


@router.post(
    "/students/{student_id}/link",
    response_model=LinkResponse,
    summary="Link an academic record to a verified identity",
)
async def link(
    student_id: uuid.UUID,
    payload: LinkRequest,
    principal: Principal = Depends(require_admin_principal),
) -> LinkResponse:
    def _work():
        with get_sync_sessionmaker()() as session:
            admin = session.get(UserAccount, principal.account_id)
            # Locked for the length of the transaction. Without this, two
            # admins linking the SAME student both read `user_id IS NULL`,
            # both update, and the second silently overwrites the first -
            # a reassignment that no constraint catches, because the column
            # is unique across rows but says nothing about a single row's
            # history. UNIQUE handles the other race (two students, one
            # account); this handles one student, two accounts.
            student = session.get(Student, student_id, with_for_update=True)
            if student is None:
                return "no_student", None
            account = find_account_by_identity(
                session, payload.identity_provider, payload.external_subject
            )
            if account is None:
                # The person has never signed in, so there is nothing to link
                # to. Provisioning here would create an account from an
                # admin's typing rather than from a verified token.
                return "no_account", None
            try:
                link_student(
                    session,
                    account,
                    student,
                    performed_by=admin,
                    reason=payload.reason,
                )
            except StudentAlreadyOwned as exc:
                return "conflict", str(exc)
            except IntegrityError:
                # Two admins raced. The database decided; this one lost.
                session.rollback()
                return "conflict", "this record was linked concurrently"
            # Ownership change and audit event commit together or not at all.
            session.commit()
            return "ok", None

    outcome, detail = await run_in_threadpool(_work)

    if outcome == "no_student":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such student."
        )
    if outcome == "no_account":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account exists for that identity; the user must sign in first.",
        )
    if outcome == "conflict":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    logger.info(
        "student_link_recorded",
        extra={"student_id": str(student_id), "by": principal.redacted()},
    )
    return LinkResponse(student_id=student_id, linked=True, event_recorded=True)


@router.post(
    "/students/{student_id}/unlink",
    response_model=LinkResponse,
    summary="Remove ownership of an academic record. Deletes no academic data.",
)
async def unlink(
    student_id: uuid.UUID,
    payload: UnlinkRequest,
    principal: Principal = Depends(require_admin_principal),
) -> LinkResponse:
    def _work():
        with get_sync_sessionmaker()() as session:
            admin = session.get(UserAccount, principal.account_id)
            student = session.get(Student, student_id, with_for_update=True)
            if student is None:
                return "no_student"
            try:
                unlink_student(
                    session, student, performed_by=admin, reason=payload.reason
                )
            except AccountNotLinked:
                return "not_linked"
            session.commit()
            return "ok"

    outcome = await run_in_threadpool(_work)

    if outcome == "no_student":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such student."
        )
    if outcome == "not_linked":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That academic record is not linked to any account.",
        )

    logger.info(
        "student_unlink_recorded",
        extra={"student_id": str(student_id), "by": principal.redacted()},
    )
    return LinkResponse(student_id=student_id, linked=False, event_recorded=True)
