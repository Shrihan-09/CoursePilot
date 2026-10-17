"""Downgrades are refused on persistent databases (Phase 7.0 incident guard)."""

from __future__ import annotations

import pytest

from app.db.migration_guard import DestructiveMigrationRefused, check_downgrade_allowed

URL = "postgresql+psycopg://u:p@localhost:5432/{}"


@pytest.mark.parametrize("name", ["coursepilot", "coursepilot_prod", "postgres",
                                  "coursepilot_test_backup", "test"])
def test_a_persistent_database_is_refused(name) -> None:
    with pytest.raises(DestructiveMigrationRefused):
        check_downgrade_allowed(URL.format(name), environ={})


@pytest.mark.parametrize("name", ["coursepilot_test", "coursepilot_migcheck_ab12_disposable"])
def test_test_and_disposable_databases_are_allowed(name) -> None:
    check_downgrade_allowed(URL.format(name), environ={})


def test_the_opt_in_names_one_exact_database() -> None:
    check_downgrade_allowed(URL.format("coursepilot"),
                            environ={"COURSEPILOT_ALLOW_DOWNGRADE": "coursepilot"})
    for value in ("1", "true", "coursepilot_other", "*"):
        with pytest.raises(DestructiveMigrationRefused):
            check_downgrade_allowed(URL.format("coursepilot"),
                                    environ={"COURSEPILOT_ALLOW_DOWNGRADE": value})
