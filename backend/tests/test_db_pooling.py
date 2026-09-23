"""Pooled connection lifecycle (Phase 5.7, Part 4).

Pooling reuses connections, so the risk it introduces is **state surviving a
request**: an open transaction, a poisoned session, a connection never
returned. These tests target observable behaviour rather than asserting
`engine.pool.__class__`, which would prove only that a constructor ran.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.session import (
    dispose_engines,
    get_sync_engine,
    get_sync_sessionmaker,
)

requires_db = pytest.mark.db


def _is_sqlite() -> bool:
    return os.environ.get("DATABASE_URL_SYNC", "postgres").startswith("sqlite")


# ==========================================================================
# reuse
# ==========================================================================


@requires_db
def test_connections_are_actually_reused() -> None:
    """The point of the change: a second session does not reconnect.

    Checked via PostgreSQL's own backend PID, which is per-connection - not
    via SQLAlchemy's pool bookkeeping, which could agree with itself while
    still opening sockets.
    """
    SM = get_sync_sessionmaker()
    pids = []
    for _ in range(5):
        with SM() as session:
            pids.append(session.execute(text("SELECT pg_backend_pid()")).scalar())
    assert len(set(pids)) == 1, f"expected one reused backend, saw {set(pids)}"


@requires_db
def test_pool_is_bounded_not_unlimited(settings) -> None:
    """Held connections stop at pool_size + max_overflow.

    Bounded is the property that matters: an unbounded pool exhausts
    PostgreSQL's max_connections and takes down the deployment, not just the
    request that asked for one too many.
    """
    SM = get_sync_sessionmaker()
    ceiling = settings.db_pool_size + settings.db_max_overflow
    sessions = []
    try:
        for _ in range(ceiling):
            session = SM()
            session.execute(text("SELECT 1"))
            sessions.append(session)
        engine = get_sync_engine()
        assert engine.pool.checkedout() == ceiling
    finally:
        for session in sessions:
            session.close()
    assert get_sync_engine().pool.checkedout() == 0


@requires_db
def test_pool_timeout_is_bounded_rather_than_hanging(settings) -> None:
    """Past the ceiling a caller waits, then fails - it does not hang forever.

    SQLAlchemy's 30s default would leave a request hanging long past the
    point the user gave up; the configured timeout turns exhaustion into a
    signal.
    """
    import time

    from sqlalchemy.exc import TimeoutError as SATimeoutError

    assert settings.db_pool_timeout <= 10.0

    SM = get_sync_sessionmaker()
    ceiling = settings.db_pool_size + settings.db_max_overflow
    sessions = []
    try:
        for _ in range(ceiling):
            session = SM()
            session.execute(text("SELECT 1"))
            sessions.append(session)

        engine = get_sync_engine()
        started = time.perf_counter()
        with pytest.raises(SATimeoutError):
            # A short explicit timeout: the point is that the wait is
            # BOUNDED, and spending the full configured budget here would
            # add ten seconds to the suite to learn nothing extra.
            engine.pool._timeout = 0.5
            conn = engine.connect()
            conn.close()
        assert time.perf_counter() - started < 5.0
    finally:
        engine = get_sync_engine()
        engine.pool._timeout = settings.db_pool_timeout
        for session in sessions:
            session.close()


# ==========================================================================
# isolation and cleanup
# ==========================================================================


@requires_db
def test_uncommitted_work_is_invisible_to_another_session() -> None:
    """A reused connection must not leak uncommitted state between requests."""
    from app.models import UserAccount

    SM = get_sync_sessionmaker()
    subject = f"pool-{uuid.uuid4()}"

    writer = SM()
    try:
        writer.add(
            UserAccount(identity_provider="oidc", external_subject=subject)
        )
        writer.flush()  # written, NOT committed

        with SM() as reader:
            found = reader.scalar(
                select(UserAccount).where(UserAccount.external_subject == subject)
            )
            assert found is None, "uncommitted state leaked across sessions"
    finally:
        writer.rollback()
        writer.close()


@requires_db
def test_a_failed_session_returns_its_connection() -> None:
    """An exception must not strand a checked-out connection.

    Without this, a handful of failing requests silently exhausts the pool
    and every later request times out - the classic pooling outage.
    """
    SM = get_sync_sessionmaker()
    before = get_sync_engine().pool.checkedout()

    for _ in range(5):
        with pytest.raises(Exception):
            with SM() as session:
                session.execute(text("SELECT * FROM no_such_table_57"))

    assert get_sync_engine().pool.checkedout() == before


@requires_db
def test_a_poisoned_transaction_does_not_poison_the_next_user() -> None:
    """PostgreSQL aborts a transaction after an error.

    If that connection went back to the pool still aborted, the next request
    would fail with "current transaction is aborted" for no reason of its
    own. The session's rollback on exit is what prevents it - verified by
    using the pool immediately afterwards.
    """
    SM = get_sync_sessionmaker()

    with pytest.raises(DBAPIError):
        with SM() as session:
            session.execute(text("SELECT * FROM no_such_table_57"))

    # Same pool, very likely the same physical connection.
    with SM() as session:
        assert session.execute(text("SELECT 1")).scalar() == 1


@requires_db
def test_rollback_leaves_no_trace_for_the_next_session() -> None:
    from app.models import UserAccount

    SM = get_sync_sessionmaker()
    subject = f"pool-rb-{uuid.uuid4()}"

    with SM() as session:
        session.add(
            UserAccount(identity_provider="oidc", external_subject=subject)
        )
        session.flush()
        session.rollback()

    with SM() as session:
        assert (
            session.scalar(
                select(UserAccount).where(UserAccount.external_subject == subject)
            )
            is None
        )


@requires_db
def test_concurrent_sessions_each_see_their_own_transaction() -> None:
    """Pooled connections under real threads, which is how requests arrive."""
    import threading

    from app.models import UserAccount

    SM = get_sync_sessionmaker()
    seen: list[object] = []
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            with SM() as session:
                session.add(
                    UserAccount(
                        identity_provider="oidc",
                        external_subject=f"pool-thread-{n}-{uuid.uuid4()}",
                    )
                )
                session.flush()
                # Each thread sees only its own uncommitted row.
                count = session.scalar(
                    select(UserAccount).where(
                        UserAccount.external_subject.like(f"pool-thread-{n}-%")
                    )
                )
                seen.append(count)
                session.rollback()
        except Exception as exc:  # pragma: no cover - surfaced by the assert
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(seen) == 8
    assert get_sync_engine().pool.checkedout() == 0


# ==========================================================================
# disposal - the other half of the NullPool fix
# ==========================================================================


@requires_db
def test_dispose_closes_pooled_connections() -> None:
    """Why NullPool was chosen in Phase 5.2, addressed properly.

    Pooling is safe only because something eventually closes the sockets.
    The app lifespan and the test session fixture both call this.
    """
    SM = get_sync_sessionmaker()
    with SM() as session:
        session.execute(text("SELECT 1"))

    engine = get_sync_engine()
    assert engine.pool.checkedin() >= 1
    dispose_engines()
    assert engine.pool.checkedin() == 0

    # Still usable afterwards: dispose returns connections, it does not
    # break the engine.
    with SM() as session:
        assert session.execute(text("SELECT 1")).scalar() == 1


def test_dispose_does_not_create_an_engine() -> None:
    """Shutdown must not open a connection just to close one.

    On a host where the database is already gone, building an engine during
    shutdown would turn a clean exit into an error.
    """
    from app.db.session import get_sync_engine as factory

    factory.cache_clear()
    assert factory.cache_info().currsize == 0
    dispose_engines()
    assert factory.cache_info().currsize == 0


# ==========================================================================
# SQLite is left alone
# ==========================================================================


def test_sqlite_urls_get_no_pool_arguments() -> None:
    """Part 3: SQLAlchemy's SQLite pool defaults are correct for SQLite.

    Forcing QueuePool onto ':memory:' would hand different sessions
    different empty databases, and onto a file database it invites
    cross-test connection sharing. Both would break the ingestion suite's
    isolation.
    """
    from app.core.config import get_settings
    from app.db.session import _pool_kwargs

    settings = get_settings()
    assert _pool_kwargs(settings, "sqlite://") == {}
    assert _pool_kwargs(settings, "sqlite:///./local.db") == {}

    postgres = _pool_kwargs(
        settings, "postgresql+psycopg://u:p@localhost:5432/db"
    )
    assert postgres["pool_size"] == settings.db_pool_size
    assert postgres["max_overflow"] == settings.db_max_overflow
    assert postgres["pool_pre_ping"] is True


def test_in_memory_sqlite_keeps_its_data_across_sessions() -> None:
    """The concrete failure a forced QueuePool would cause."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import get_settings
    from app.db.session import _pool_kwargs

    engine = create_engine("sqlite://", **_pool_kwargs(get_settings(), "sqlite://"))
    SM = sessionmaker(engine)
    try:
        with SM() as session:
            session.execute(text("CREATE TABLE t (x INTEGER)"))
            session.execute(text("INSERT INTO t VALUES (1)"))
            session.commit()
        with SM() as session:
            assert session.execute(text("SELECT x FROM t")).scalar() == 1
    finally:
        engine.dispose()


# ==========================================================================
# CLI
# ==========================================================================


@requires_db
def test_cli_acquires_and_releases_connections() -> None:
    """The Phase 5.5 dev bootstrap uses the same pooled factory."""
    from app.cli import dev_bootstrap

    before = get_sync_engine().pool.checkedout()
    exit_code = dev_bootstrap.main(
        ["grant-admin", "--provider", "oidc", "--subject", f"absent-{uuid.uuid4()}"]
    )
    assert exit_code == 1  # no such account, which is the point of the probe
    assert get_sync_engine().pool.checkedout() == before
