"""Tests may only connect to a database whose name ends in `_test` (task 1 incident guard).

During task 1 a full backend test run started without the test-database
environment variables. The fixtures still used `coursepilot_test`, but the
app's own sessions fell back to the configured development database, and API
tests wrote six fabricated accounts into the developer's real data.

`install()` is called by the backend and ingestion test conftests before any
engine exists. It hooks SQLAlchemy's pool `connect` event, which every engine
in the process passes through - a fixture's, the app's, a script's - and
refuses any PostgreSQL connection whose database name does not end in
`_test`, loudly and before a single statement runs. SQLite (the in-memory
test databases) has no database name and is allowed.
"""

from __future__ import annotations

import sqlite3

from sqlalchemy import event
from sqlalchemy.pool import Pool

REQUIRED_SUFFIX = "_test"
FIX = ("Point DATABASE_URL, DATABASE_URL_SYNC and TEST_DATABASE_URL at the test database, "
       "e.g. postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test")


class NonTestDatabaseRefused(RuntimeError):
    """A test tried to connect to a database that is not a *_test database."""


def check_test_database(name: str | None) -> None:
    if not name or not name.endswith(REQUIRED_SUFFIX):
        raise NonTestDatabaseRefused(
            f"Refusing to connect: tests may only use a database whose name ends in "
            f"'{REQUIRED_SUFFIX}', and this connection is to {name!r}. {FIX}.")


def database_name(dbapi_connection) -> str | None:
    """The PostgreSQL database name of a raw DBAPI connection; None for SQLite."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        return None
    raw = getattr(dbapi_connection, "driver_connection", dbapi_connection)  # async adapter
    info = getattr(raw, "info", None)
    return getattr(info, "dbname", "") if info is not None else ""


def _on_connect(dbapi_connection, _connection_record) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        return
    try:
        check_test_database(database_name(dbapi_connection))
    except NonTestDatabaseRefused:
        # Close what was refused: nothing may run on it, and an unclosed
        # connection would only resurface later as a finalizer warning.
        try:
            dbapi_connection.close()
        finally:
            raise


def install() -> None:
    """Idempotent: safe to call from several conftests in one process."""
    if not event.contains(Pool, "connect", _on_connect):
        event.listen(Pool, "connect", _on_connect)


__all__ = ["NonTestDatabaseRefused", "check_test_database", "database_name", "install"]
