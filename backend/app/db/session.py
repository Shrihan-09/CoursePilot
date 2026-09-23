"""Engines, session factories and connection lifecycle.

## The lifecycle

```
request
  |
  v  session factory (one engine per process, lru_cached)
SQLAlchemy Session
  |
  v  checkout from a BOUNDED pool
pooled connection
  |
  v  transaction
commit / rollback  ->  connection RETURNED to the pool (not closed)
  |
  v  process shutdown
dispose_engines()  ->  sockets actually closed
```

## Why the sync engine stopped using NullPool (Phase 5.7)

Phase 5.2 chose `NullPool` for a real reason, quoted from the code it
replaced:

> a pooled engine here holds connections open for the life of the process
> and leaks them at interpreter exit (a ResourceWarning in tests)

That reason was accurate and the remedy was aimed at the wrong end. The
problem was never that connections were *pooled*; it was that nothing ever
**disposed** the engine. `NullPool` fixed the symptom by ensuring there was
never anything to dispose - and charged every single request ~21 ms of
connection setup to do it (measured in Phase 5.6: a bare session plus
`SELECT 1` cost 20.93 ms).

So Phase 5.7 fixes the actual cause: a bounded pool, plus `dispose_engines()`
wired into the application's shutdown and into test teardown. The
ResourceWarning the original comment describes is what `dispose_engines()`
exists to prevent, and a test asserts it does.

Note the async engine was **already** pooled (`AsyncAdaptedQueuePool`, 5+10)
and had been since it was written. Pooling is not new here; it was simply
never extended to the sync engine that does the expensive work.

## Why SQLite is left alone

Pool arguments are applied only to non-SQLite URLs. SQLAlchemy picks a
pool for SQLite deliberately - `SingletonThreadPool` for `:memory:`, so the
in-memory database survives between sessions on one thread, and a
file database has its own locking characteristics. Forcing `QueuePool` onto
`:memory:` would hand different sessions *different empty databases*, and
forcing it onto a file database invites cross-test connection sharing.
Letting SQLAlchemy choose preserves existing test isolation exactly.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _pool_kwargs(settings: Settings, url: str) -> dict[str, Any]:
    """Pool configuration, or nothing at all for SQLite.

    Returning `{}` is not a fallback - it is the correct answer. SQLAlchemy's
    SQLite defaults are chosen for SQLite's semantics, and overriding them
    breaks in-memory databases and test isolation.
    """
    if _is_sqlite(url):
        return {}
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": settings.db_pool_recycle_seconds,
        # One round trip per checkout, in exchange for surviving a Postgres
        # restart during local development instead of handing the caller a
        # dead connection. Far cheaper than the connection it replaces.
        "pool_pre_ping": True,
    }


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.debug,
        **_pool_kwargs(settings, settings.database_url),
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. One session per request.

    The `async with` is what returns the connection to the pool, on the
    success path and on the exception path alike. A request that raises does
    not leak a checked-out connection.
    """
    async with get_sessionmaker()() as session:
        yield session


@lru_cache
def get_sync_engine():
    """The synchronous engine. One per process, bounded pool.

    Exposed separately from the session factory so shutdown and tests have
    something to dispose - the omission that made `NullPool` look necessary.
    """
    from sqlalchemy import create_engine

    settings = get_settings()
    return create_engine(
        settings.database_url_sync,
        **_pool_kwargs(settings, settings.database_url_sync),
    )


@lru_cache
def get_sync_sessionmaker() -> sessionmaker[Session]:
    """Synchronous sessions, for code that is synchronous by design.

    The audit engine, the optimizer and the search index are all sync - they
    are CPU-bound pure computation over loaded rows, and making them async
    would add colour without adding concurrency. A request that needs them
    runs them in a worker thread with one of these sessions rather than
    rewriting the deterministic core to suit the transport.
    """
    return sessionmaker(get_sync_engine(), expire_on_commit=False)


def dispose_engines() -> None:
    """Close every pooled connection this process holds.

    Called on application shutdown and in test teardown. Without it a pool
    keeps sockets open until the interpreter exits, which is exactly the
    ResourceWarning that made `NullPool` attractive in Phase 5.2.

    Safe to call when no engine was ever built: the `lru_cache` is inspected
    rather than invoked, so this never CREATES an engine just to dispose it -
    which would open a connection during shutdown, and fail on a machine
    where the database is already gone.
    """
    if get_sync_engine.cache_info().currsize:
        get_sync_engine().dispose()
        logger.debug("sync_engine_disposed")


async def dispose_engines_async() -> None:
    """Async counterpart, plus the sync engines. Used by the app lifespan."""
    dispose_engines()
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
        logger.debug("async_engine_disposed")


def reset_engines() -> None:
    """Test hook: dispose and forget, so the next call rebuilds from settings.

    Needed because the engines are `lru_cache`d against settings read at
    construction time; a test that changes `DATABASE_URL_SYNC` would
    otherwise keep talking to the previous database.
    """
    dispose_engines()
    get_sync_engine.cache_clear()
    get_sync_sessionmaker.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
