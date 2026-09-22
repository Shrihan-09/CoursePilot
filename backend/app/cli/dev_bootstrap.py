"""Development bootstrap for the first administrator (Phase 5.5, Part 18).

## The chicken-and-egg problem

Only an administrator can link students. Administrative authority lives in
`user_account.is_admin`, which no request can set. So on a fresh database
nobody can do anything, and something outside the request path has to grant
the first `is_admin`.

## Why this is a command and not an endpoint

It could have been a bootstrap route guarded by a setting. It is not,
because **a route that must never run in production is still a route in
production** - reachable, fuzzable, and one misread environment variable
away from granting admin to a stranger.

A command has no listener. It runs only where someone already has shell
access and database credentials, and at that point they could set the column
by hand anyway; this just makes the supported way the easy way.

It additionally refuses to run when `COURSEPILOT_ENV` is production, so the
obvious misuse - running it against the wrong `DATABASE_URL` - fails loudly
instead of quietly minting an administrator.

## Usage

```
python -m app.cli.dev_bootstrap grant-admin --provider dev --subject alice
python -m app.cli.dev_bootstrap link --provider dev --subject alice \
       --student-id <uuid> --as-admin-subject alice
```

`grant-admin` does not create accounts. The person must have signed in at
least once, so even here an account exists because a verifier accepted a
credential, never because someone typed a name.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from app.core.config import Environment, get_settings
from app.db.session import get_sync_sessionmaker
from app.models import Student
from app.services.accounts import (
    StudentAlreadyOwned,
    find_account_by_identity,
    link_student,
)


def _refuse_in_production() -> None:
    settings = get_settings()
    if settings.coursepilot_env is Environment.PRODUCTION:
        sys.exit(
            "refusing to run: COURSEPILOT_ENV is production. "
            "This command is a development bootstrap, not an admin tool."
        )


def _grant_admin(args: argparse.Namespace) -> int:
    with get_sync_sessionmaker()() as session:
        account = find_account_by_identity(session, args.provider, args.subject)
        if account is None:
            print(
                f"no account for {args.provider}:{args.subject} - "
                "that person must sign in once first",
                file=sys.stderr,
            )
            return 1
        account.is_admin = True
        session.commit()
        print(f"granted admin to {args.provider}:{args.subject}")
    return 0


def _link(args: argparse.Namespace) -> int:
    with get_sync_sessionmaker()() as session:
        account = find_account_by_identity(session, args.provider, args.subject)
        if account is None:
            print(f"no account for {args.provider}:{args.subject}", file=sys.stderr)
            return 1
        performed_by = account
        if args.as_admin_subject:
            performed_by = find_account_by_identity(
                session, args.provider, args.as_admin_subject
            )
            if performed_by is None:
                print("no account for the acting administrator", file=sys.stderr)
                return 1
        student = session.get(Student, uuid.UUID(args.student_id))
        if student is None:
            print("no such student", file=sys.stderr)
            return 1
        try:
            link_student(
                session,
                account,
                student,
                performed_by=performed_by,
                reason="dev bootstrap",
            )
        except StudentAlreadyOwned as exc:
            # The same refusal the API gives. The bootstrap path is not a way
            # around the rule that ownership is never silently transferred.
            print(str(exc), file=sys.stderr)
            return 1
        session.commit()
        print(f"linked student {student.id} to {args.provider}:{args.subject}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _refuse_in_production()

    parser = argparse.ArgumentParser(prog="dev_bootstrap")
    sub = parser.add_subparsers(dest="command", required=True)

    grant = sub.add_parser("grant-admin", help="mark an existing account as admin")
    grant.add_argument("--provider", required=True)
    grant.add_argument("--subject", required=True)
    grant.set_defaults(func=_grant_admin)

    link = sub.add_parser("link", help="link an academic record, with an audit event")
    link.add_argument("--provider", required=True)
    link.add_argument("--subject", required=True)
    link.add_argument("--student-id", required=True)
    link.add_argument("--as-admin-subject", default=None)
    link.set_defaults(func=_link)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
