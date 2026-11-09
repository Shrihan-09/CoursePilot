"""Test output stays out of the repository and OneDrive (Prompt 11 Part B, item 2)."""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from app.core.test_output_guard import REPO_ROOT, UnsafeBasetemp, check_basetemp


def test_default_and_system_temp_basetemps_are_allowed() -> None:
    check_basetemp(None)
    check_basetemp(pathlib.Path(tempfile.gettempdir()) / "coursepilot-pytest-new")


@pytest.mark.parametrize("target", [
    REPO_ROOT / ".tmp_11a_backend_final",
    REPO_ROOT / "ingestion" / ".tmp_run",
    REPO_ROOT / "data" / "raw",
    REPO_ROOT,
])
def test_a_basetemp_inside_the_repository_is_refused(target) -> None:
    with pytest.raises(UnsafeBasetemp, match="Refusing --basetemp"):
        check_basetemp(target)


def test_a_onedrive_basetemp_is_refused(tmp_path) -> None:
    with pytest.raises(UnsafeBasetemp):
        check_basetemp(tmp_path / "OneDrive" / "scratch", repo_root=tmp_path / "elsewhere")
