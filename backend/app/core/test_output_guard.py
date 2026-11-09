"""Test output must live in system temp, never inside the repository or OneDrive.

Pytest's default `tmp_path` root is already the system temp folder
(`%TEMP%\\pytest-of-<user>` / `/tmp/pytest-of-<user>`), outside the repository
and outside OneDrive. An explicit `--basetemp` replaces that root, and pytest
DELETES the folder it is given before each run. Earlier runs pointed it at
`.tmp_*` folders inside the OneDrive repository, which syncs every scratch
file and puts a cleanup-capable path next to `data/raw/` archives.

`check_basetemp()` is called from the backend and ingestion conftests. It
refuses a basetemp inside the repository or under a OneDrive folder, before
any test creates a file. It never deletes anything itself.
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


class UnsafeBasetemp(RuntimeError):
    """--basetemp points inside the repository or OneDrive."""


def check_basetemp(basetemp: str | pathlib.Path | None, *, repo_root: pathlib.Path = REPO_ROOT
                   ) -> None:
    if not basetemp:
        return                      # pytest's default: the system temp folder
    resolved = pathlib.Path(basetemp).expanduser().resolve()
    root = repo_root.resolve()
    inside_repo = resolved == root or root in resolved.parents
    onedrive = any(part.lower().startswith("onedrive") for part in resolved.parts)
    if inside_repo or onedrive:
        raise UnsafeBasetemp(
            f"Refusing --basetemp={resolved}: pytest deletes this folder before each run, "
            "and test output must not live in the repository or OneDrive. Omit --basetemp "
            "(pytest then uses the system temp folder) or point it at a NEW folder under "
            "the system temp directory.")


def pytest_configure_hook(config) -> None:
    check_basetemp(config.getoption("basetemp", None))


__all__ = ["UnsafeBasetemp", "check_basetemp", "pytest_configure_hook"]
