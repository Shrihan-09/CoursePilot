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
