"""Account provisioning and student linking (Phase 5.4, Parts 12-13).

## The distinction this module exists to hold

> A verified identity proves **who someone is**. It does not prove **which
> academic record belongs to them**.

Those are separate facts, and collapsing them is how Phase 5.3's limitation
would come back. So:

```
verified token -> provision UserAccount       automatic, safe
UserAccount    -> link to a Student           NOT automatic
```

Provisioning is safe because a `UserAccount` is created *from* the verified
subject and owns nothing until linked. Linking is an ownership claim over
real academic data, and nothing in a token establishes it. A `sub` is an
opaque provider key; `Student.external_ref` is a label the ingestion path
wrote. Matching them would be a guess wearing the costume of a lookup.

## So what happens to a new account?

It exists, and it owns nothing. The API reports that state explicitly rather
than inventing a record or returning an empty audit that looks like "no
progress". `AccountNotLinked` is a normal outcome, not an error condition.

## Who may link? (Phase 5.5)

**An administrator, and nobody else.** The mechanisms were weighed against
what CoursePilot can actually verify today:

| mechanism | verdict |
|---|---|
| verified student number in an IdP claim | **unavailable** - Rutgers SSO is unverified and releases no such claim to this project |
| one-time code through a trusted channel | **deferred** - there is no trusted delivery channel (no email, SMS or registrar integration). A code we generate and an admin relays adds a secret lifecycle - storage, hashing, expiry, brute-force surface - without adding verification beyond what the admin already did in person |
| **administrator-assisted** | **chosen** - the admin verifies identity out of band; no secret to store, no delivery channel, no brute-force surface |

The admin names the person by their **identity** (provider + subject), not by
an account id, so nothing in the workflow requires enumerating accounts.

`link_student` still refuses to move an already-owned record, so no path -
API, bootstrap or test - can silently reassign someone's transcript.

## Every mutation writes an audit event, in the same transaction

Linking changes who may read a transcript, so "it was linked but we do not
know by whom" is not an acceptable state. The event is written inside the
caller's transaction: either both land or neither does.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedPrincipal
from app.models import (
    LINK_ACTION_LINKED,
    LINK_ACTION_UNLINKED,
    Student,
    StudentLinkEvent,
    UserAccount,
)

logger = logging.getLogger(__name__)


class AccountDisabled(Exception):
    """The account exists but has been disabled."""


class AccountNotLinked(Exception):
    """Authenticated, but no academic record is linked to this account.

    A normal state for a new user, not a failure of authentication.
    """


class StudentAlreadyOwned(Exception):
    """Refusing to reassign an academic record that already has an owner."""


def resolve_account(session: Session, principal: AuthenticatedPrincipal) -> UserAccount:
    """Find or provision the account for a verified principal.

    The `(identity_provider, external_subject)` unique constraint is the real
    guard here. Two concurrent first-logins both see "no account" and both
    insert; one wins, the loser catches IntegrityError and re-reads. An
    application-level check alone would produce duplicate accounts under
    exactly the race it was written to prevent.
    """
    stmt = select(UserAccount).where(
        UserAccount.identity_provider == principal.provider,
        UserAccount.external_subject == principal.subject,
    )
    account = session.scalar(stmt)
    if account is None:
        account = UserAccount(
            identity_provider=principal.provider,
            external_subject=principal.subject,
        )
        session.add(account)
        try:
            session.flush()
            logger.info(
                "account_provisioned",
                extra={
                    "provider": principal.provider,
                    "subject_hash": principal.redacted_subject(),
                },
            )
        except IntegrityError:
            session.rollback()
            account = session.scalar(stmt)
            if account is None:  # pragma: no cover - constraint says otherwise
                raise

    if not account.is_active:
        raise AccountDisabled("account is disabled")
    return account


def resolve_owned_student(session: Session, account: UserAccount) -> Student:
    """The student this account owns. Never a student it merely names.

    The query is keyed on `user_id`. There is no code path that reaches a
    student by a client-supplied reference, which is what makes cross-user
    access impossible rather than merely checked.
    """
    student = session.scalar(select(Student).where(Student.user_id == account.id))
    if student is None:
        raise AccountNotLinked("no academic record is linked to this account")
    return student


class NotAuthorized(Exception):
    """The caller is not an administrator."""


def link_student(
    session: Session,
    account: UserAccount,
    student: Student,
    *,
    performed_by: UserAccount | None = None,
    reason: str | None = None,
) -> Student:
    """Link an academic record to an account, with an audit event.

    Refuses to move an already-owned student, so no caller - API, bootstrap
    or test - becomes a reassignment path. The database UNIQUE on
    `student.user_id` backs up the one-student-per-account half, and is the
    authority when two admins act concurrently: both application checks can
    pass, and only one write survives.

    The event is flushed in the SAME transaction as the ownership change, so
    there is no state where a link exists without a record of who made it.
    """
    if student.user_id is not None and student.user_id != account.id:
        raise StudentAlreadyOwned(
            "this academic record is already linked to another account"
        )
    existing = session.scalar(select(Student).where(Student.user_id == account.id))
    if existing is not None and existing.id != student.id:
        raise StudentAlreadyOwned("this account already owns an academic record")

    already = student.user_id == account.id
    student.user_id = account.id
    if not already:
        session.add(
            StudentLinkEvent(
                student_id=student.id,
                user_account_id=account.id,
                performed_by_id=(performed_by or account).id,
                action=LINK_ACTION_LINKED,
                reason=(reason or None),
            )
        )
    session.flush()
    logger.info(
        "student_linked",
        extra={
            "account_id": str(account.id),
            "performed_by": str((performed_by or account).id),
        },
    )
    return student


def unlink_student(
    session: Session,
    student: Student,
    *,
    performed_by: UserAccount,
    reason: str | None = None,
) -> Student:
    """Remove ownership. Deletes NO academic data.

    Only `student.user_id` is cleared. The student row, its courses and its
    history are untouched - `StudentCourse` cascades from `Student`, so
    deleting the student instead of unlinking it would destroy a transcript.
    Unlinking is not deletion and must never be implemented as one.

    A later request from that account gets the ordinary unlinked response,
    because the account now owns nothing.
    """
    if student.user_id is None:
        raise AccountNotLinked("this academic record is not linked to any account")

    previous = student.user_id
    student.user_id = None
    session.add(
        StudentLinkEvent(
            student_id=student.id,
            user_account_id=previous,
            performed_by_id=performed_by.id,
            action=LINK_ACTION_UNLINKED,
            reason=(reason or None),
        )
    )
    session.flush()
    logger.info(
        "student_unlinked",
        extra={"account_id": str(previous), "performed_by": str(performed_by.id)},
    )
    return student


def require_admin(account: UserAccount) -> UserAccount:
    """Administrative authority comes from the database, never a request."""
    if not account.is_admin:
        raise NotAuthorized("administrative authority is required")
    return account


def find_account_by_identity(
    session: Session, identity_provider: str, external_subject: str
) -> UserAccount | None:
    """Resolve the account an admin names by IDENTITY rather than by id.

    Keeps account ids out of the workflow entirely: an admin verifies a
    person's NetID in person, so that is what they type. Nothing here
    provisions - the person must have signed in at least once, which is
    itself evidence their identity provider accepted them.
    """
    return session.scalar(
        select(UserAccount).where(
            UserAccount.identity_provider == identity_provider,
            UserAccount.external_subject == external_subject,
        )
    )


__all__ = [
    "AccountDisabled",
    "AccountNotLinked",
    "NotAuthorized",
    "StudentAlreadyOwned",
    "find_account_by_identity",
    "link_student",
    "require_admin",
    "resolve_account",
    "resolve_owned_student",
    "unlink_student",
]
