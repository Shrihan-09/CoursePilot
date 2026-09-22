"""Async engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.debug,
        pool_pre_ping=True,  # survives Postgres restarts during local dev
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. One session per request."""
    async with get_sessionmaker()() as session:
        yield session


@lru_cache
def get_sync_sessionmaker() -> sessionmaker[Session]:
    """Synchronous sessions, for code that is synchronous by design.

    The audit engine, the optimizer and the search index are all sync - they
    are CPU-bound pure computation over loaded rows, and making them async
    would add colour without adding concurrency. A request that needs them
    runs them in a worker thread with one of these sessions rather than
    rewriting the deterministic core to suit the transport.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    settings = get_settings()
    # NullPool: the connection closes when the session does. A pooled engine
    # here holds connections open for the life of the process and leaks them
    # at interpreter exit (a ResourceWarning in tests). Sync work is
    # request-scoped and infrequent, so paying connection setup per request
    # is the right trade for not holding sockets open indefinitely.
    engine = create_engine(
        settings.database_url_sync, poolclass=NullPool, pool_pre_ping=True
    )
    return sessionmaker(engine, expire_on_commit=False)
