"""Shared test fixtures.

Design rule: the default test client requires no database and no network. A
test suite that silently needs Postgres running is a suite people stop
running. Database-backed tests opt in explicitly and are marked `db`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

# Set before importing app config so Settings picks these up.
os.environ.setdefault("COURSEPILOT_ENV", "ci")
os.environ.setdefault("LLM_PROVIDER", "echo")
# Development authentication, for tests only. Explicitly opt-in and refused
# outright when the environment is production - see app/api/security.py.
os.environ.setdefault("DEV_AUTH_ENABLED", "true")


@pytest.fixture
def settings():
    from app.core.config import get_settings

    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """In-process HTTP client. No network, no server."""
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Rate limiters are process-global, so one test must not spend another
    test's budget. Reset around every test."""
    from app.api.security import reset_limiters

    reset_limiters()
    yield
    reset_limiters()


def auth(student_ref: str = "student-a") -> dict[str, str]:
    """Development credential for a given student."""
    return {"Authorization": f"Bearer devtoken:{student_ref}"}
