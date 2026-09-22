"""Account identity and ownership (Phase 5.4).

## Why this table did not exist before

Phase 5.3 derived a student from the credential rather than the request body,
which stopped one caller naming another. But the credential *asserted* an
identity; nothing verified it, and `Student` had no owner. This is the model
that makes ownership real.

## External identity is not internal identity

```
Rutgers / OIDC provider
        |  iss + sub
        v
UserAccount.id          <- CoursePilot's stable internal identity
        |  owns
        v
Student.id
```

`(identity_provider, external_subject)` is how an incoming token finds its
account. `UserAccount.id` is what the rest of CoursePilot uses. Keeping them
separate matters because they change for different reasons:

  * a provider's `sub` changes if the university migrates identity systems,
    or if a person's account is recreated;
  * `UserAccount.id` must never change, because rate-limit keys, logs and any
    future per-user data hang off it.

Making the provider subject the primary key would weld CoursePilot's internal
graph to one vendor's identifier - exactly the coupling the provider-neutral
auth boundary exists to prevent.

## Table name

`user_account`, not `user`: **`user` is a reserved word in PostgreSQL**, so
the table would have to be quoted everywhere. The project's other tables are
unquoted singulars, and a table you cannot type without quotes is a
long-running papercut.

## Cardinality: one account owns AT MOST one student

Reasoned rather than assumed:

  * the audit engine evaluates exactly ONE `ProgramVersion` per `Student`
    (established in Phase 4), so a second programme would need a second
    `Student` row;
  * but CoursePilot has no dual-programme support today, and no product
    decision has been made about auditing two programmes at once;
  * an academic record is never shared between people, so many-accounts-to-
    one-student is never correct.

So: `student.user_id` is nullable with a **UNIQUE** constraint. Nullable
because an unlinked `Student` is a real state (see the linking design in
DATA_MODEL.md section 26); unique because one account must not accumulate
student records. Relaxing this later means dropping a constraint, which is a
smaller migration than adding one to data that has already violated it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

#: Identifies which identity system a subject came from. An open set: the
#: same subject string from two providers is two different people, so the
#: provider is part of the identity rather than an annotation on it.
PROVIDER_DEV = "dev"
PROVIDER_OIDC = "oidc"


class UserAccount(Base, TimestampMixin):
    """One authenticated person.

    Deliberately holds no profile: no name, no email, no NetID. CoursePilot
    does not need them to run an audit, and student data is a liability. If a
    display name is ever required it should come from the token at request
    time rather than being copied into this table.
    """

    __tablename__ = "user_account"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    #: Which identity system asserted this subject.
    identity_provider: Mapped[str] = mapped_column(String(32))
    #: The provider's `sub` claim. Opaque on purpose - it is a key, not a
    #: name, and nothing in CoursePilot should parse it.
    external_subject: Mapped[str] = mapped_column(String(255))

    #: Lifecycle. Disabling is expressed as a timestamp rather than a boolean
    #: so "when" survives, and so a disabled account is never silently
    #: re-enabled by a default.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # passive_deletes="all": do NOT let the ORM null out student.user_id when
    # an account is deleted. SQLAlchemy's default is to orphan the child
    # first, which silently defeats the RESTRICT this FK was given - the
    # delete would succeed and an academic record would quietly lose its
    # owner. Deferring entirely to the database makes the constraint real.
    student: Mapped[Student | None] = relationship(  # noqa: F821
        back_populates="user", passive_deletes="all"
    )

    __table_args__ = (
        # The final authority against a race between two concurrent
        # first-logins for the same subject. An application-level
        # "if not exists: create" cannot provide this.
        UniqueConstraint(
            "identity_provider", "external_subject", name="uq_user_account_identity"
        ),
        Index("ix_user_account_identity", "identity_provider", "external_subject"),
    )

    @property
    def is_active(self) -> bool:
        return self.disabled_at is None

    def __repr__(self) -> str:
        return f"<UserAccount {self.identity_provider}:{self.id}>"


def student_owner_column() -> Mapped[uuid.UUID | None]:
    """The column added to `Student`. Defined here to keep ownership in one
    file; `Student` imports it."""
    return mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
        index=True,
    )


__all__ = ["PROVIDER_DEV", "PROVIDER_OIDC", "UserAccount", "student_owner_column"]
