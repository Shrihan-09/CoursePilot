"""Alembic environment.

Uses the *sync* database URL: Alembic's migration runner is synchronous, and
mixing it with the app's async engine adds complexity for no benefit. Both
URLs point at the same database.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.base import Base

# Importing app.models registers every ORM class on Base.metadata so that
# `alembic revision --autogenerate` can see them. Currently a no-op.
import app.models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url_sync)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _refuse_destructive_commands() -> None:
    """A downgrade against a persistent database is refused BEFORE anything
    connects (app.db.migration_guard; Phase 7.0 incident)."""
    from app.db.migration_guard import check_downgrade_allowed

    # `alembic.context` is a module proxy; the live EnvironmentContext - and
    # the command function alembic passed it - is `context._proxy`.
    fn = context._proxy.context_opts.get("fn")
    if getattr(fn, "__name__", "") == "downgrade":
        check_downgrade_allowed(config.get_main_option("sqlalchemy.url"))


_refuse_destructive_commands()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Without these, autogenerate misses column type and server-default
            # changes, silently producing incomplete migrations.
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
