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
os.environ.setdefault("AUTH_PROVIDER", "dev")


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


def auth(subject: str = "subject-a") -> dict[str, str]:
    """Development credential. NOT authentication - see app/api/auth.py."""
    return {"Authorization": f"Bearer dev:{subject}"}


@pytest.fixture
async def authenticated_client(request) -> AsyncIterator[AsyncClient]:
    """A client whose requests carry an authenticated principal.

    Overrides the `get_principal` DEPENDENCY rather than weakening
    authentication, which is what Phase 5.4's brief asks for: the route still
    depends on the real dependency, and the override supplies a principal the
    way a verified token would.

    Using the override also keeps this suite free of a database. Account
    provisioning talks to Postgres, so the full credential -> account path is
    covered by the `db`-marked tests instead.
    """
    import uuid as _uuid

    from app.api.security import Principal, get_principal
    from app.main import create_app

    marker = request.node.get_closest_marker("principal")
    account_id = _uuid.UUID(int=1) if marker is None else marker.args[0]

    app = create_app()
    app.dependency_overrides[get_principal] = lambda: Principal(
        account_id=account_id,
        subject="subject-a",
        issuer="coursepilot-dev",
        provider="dev",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
