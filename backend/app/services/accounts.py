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

## Who may link?

Deliberately nobody, through the public API. The safe mechanisms all require
something this project does not have yet:

| mechanism | what it needs |
|---|---|
| verified student number in the token | Rutgers claim release, requires approval |
| one-time code issued through a trusted channel | an out-of-band channel |
| administrator-assisted linking | an admin surface and an audit trail |

`link_student` is therefore an internal, explicit call used by bootstrap and
tests. It is not reachable from a request, and it refuses to move a student
that is already owned - so no path, however it is invoked, can silently
reassign an existing academic record to a different person.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedPrincipal
from app.models import Student, UserAccount

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


def link_student(session: Session, account: UserAccount, student: Student) -> Student:
    """Link an academic record to an account. INTERNAL - never from a request.

    Refuses to move an already-owned student, so bootstrap and tests cannot
    become a reassignment path either. The database's UNIQUE on
    `student.user_id` backs this up for the one-student-per-account half.
    """
    if student.user_id is not None and student.user_id != account.id:
        raise StudentAlreadyOwned(
            "this academic record is already linked to another account"
        )
    existing = session.scalar(select(Student).where(Student.user_id == account.id))
    if existing is not None and existing.id != student.id:
        raise StudentAlreadyOwned("this account already owns an academic record")

    student.user_id = account.id
    session.flush()
    logger.info("student_linked", extra={"account_id": str(account.id)})
    return student


__all__ = [
    "AccountDisabled",
    "AccountNotLinked",
    "StudentAlreadyOwned",
    "link_student",
    "resolve_account",
    "resolve_owned_student",
]
