"""Tests can only connect to a *_test database (task 1 incident guard)."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import Pool

from app.db import test_db_guard as guard

from .test_multi_program import requires_db


@pytest.mark.parametrize("name", ["coursepilot", "coursepilot_prod", "postgres",
                                  "coursepilot_test_backup", "test", "", None])
def test_a_database_not_ending_in_test_is_refused_with_a_clear_message(name) -> None:
    with pytest.raises(guard.NonTestDatabaseRefused) as refused:
        guard.check_test_database(name)
    assert "ends in '_test'" in str(refused.value)
    assert "TEST_DATABASE_URL" in str(refused.value)


def test_a_test_database_is_allowed() -> None:
    guard.check_test_database("coursepilot_test")


def test_the_guard_is_installed_for_every_engine_in_this_process() -> None:
    assert event.contains(Pool, "connect", guard._on_connect)
    guard.install()                                     # idempotent
    assert event.contains(Pool, "connect", guard._on_connect)


def test_the_hook_reads_the_database_name_of_every_driver_shape() -> None:
    sync = SimpleNamespace(info=SimpleNamespace(dbname="coursepilot"), close=lambda: None)
    adapted = SimpleNamespace(driver_connection=SimpleNamespace(
        info=SimpleNamespace(dbname="coursepilot_test")))
    with pytest.raises(guard.NonTestDatabaseRefused):
        guard._on_connect(sync, None)
    guard._on_connect(adapted, None)
    guard._on_connect(sqlite3.connect(":memory:"), None)    # SQLite has no name


@requires_db
# psycopg's deallocator on the refused (already closed) connection reports an
# unraisable no-op; only that warning is ignored, only here.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_a_real_connection_to_the_dev_database_is_refused_before_any_statement() -> None:
    engine = create_engine(
        "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot", future=True)
    try:
        with pytest.raises(guard.NonTestDatabaseRefused), engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    finally:
        engine.dispose()
        import gc

        gc.collect()        # finalize the refused connection inside this test


@requires_db
def test_a_real_connection_to_the_test_database_works() -> None:
    engine = create_engine(
        "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test",
        future=True)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT current_database()")).scalar() == "coursepilot_test"
    finally:
        engine.dispose()
