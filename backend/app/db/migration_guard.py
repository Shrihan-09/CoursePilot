"""Refuse destructive migration commands against a persistent database.

Phase 7.0 incident: a chained `alembic upgrade head && ... downgrade -1`
ran after the upgrade had FAILED, so `-1` downgraded the PREVIOUS phase's
migration on the development database and dropped its tables. A downgrade
deletes schema and data; it belongs on a throwaway database.

The rule: `alembic downgrade` runs only when the target database's name ends
in `_test` or `_disposable`, or when COURSEPILOT_ALLOW_DOWNGRADE names that
exact database (a deliberate, per-database opt-in - never a blanket switch).
`scripts/verify_migration_roundtrip.py` creates a `*_disposable` database
for upgrade -> downgrade -> re-upgrade checks and drops it afterwards.
"""

from __future__ import annotations

import os

from sqlalchemy.engine import make_url

SAFE_SUFFIXES = ("_test", "_disposable")
OPT_IN_ENV = "COURSEPILOT_ALLOW_DOWNGRADE"


class DestructiveMigrationRefused(RuntimeError):
    pass


def check_downgrade_allowed(url: str, environ: dict | None = None) -> None:
    """Raise unless a downgrade may run against `url`."""
    environ = os.environ if environ is None else environ
    name = make_url(url).database or ""
    if name.endswith(SAFE_SUFFIXES):
        return
    if environ.get(OPT_IN_ENV) == name:
        return
    raise DestructiveMigrationRefused(
        f"Refusing 'alembic downgrade' on database {name!r}: downgrades drop tables and "
        f"data. Use a *_test or *_disposable database "
        f"(scripts/verify_migration_roundtrip.py), or set {OPT_IN_ENV}={name} to confirm "
        "this specific database deliberately.")


__all__ = ["OPT_IN_ENV", "SAFE_SUFFIXES", "DestructiveMigrationRefused",
           "check_downgrade_allowed"]
