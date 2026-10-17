"""upgrade -> downgrade -> re-upgrade, on a THROWAWAY database (Phase 7.0).

Creates `coursepilot_migcheck_<hex>_disposable` on the PostgreSQL server of
the given admin URL, runs, in that database only:

    alembic upgrade head
    alembic downgrade <head's down_revision>     (one step, named explicitly)
    alembic upgrade head
    alembic check

and drops the database whatever happens. The development and test databases
are never touched - see app/db/migration_guard.py for why this exists.

Usage (from the repository root):

    python scripts/verify_migration_roundtrip.py \
        [--server postgresql+psycopg://coursepilot:coursepilot@localhost:5432/postgres]
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = pathlib.Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"


def _alembic(url: str, *args: str) -> str:
    env = {**os.environ, "DATABASE_URL": url, "DATABASE_URL_SYNC": url}
    proc = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env,
                          capture_output=True, text=True, check=False)
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise SystemExit(f"alembic {' '.join(args)} FAILED:\n{out[-2000:]}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--server", default="postgresql+psycopg://coursepilot:coursepilot@"
                                            "localhost:5432/postgres")
    args = parser.parse_args()
    server = make_url(args.server)
    name = f"coursepilot_migcheck_{uuid.uuid4().hex[:8]}_disposable"
    target = server.set(database=name).render_as_string(hide_password=False)
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "app" / "db" / "migrations"))
        script = ScriptDirectory.from_config(cfg)
        head = script.get_current_head()
        previous = script.get_revision(head).down_revision
        _alembic(target, "upgrade", "head")
        print(f"upgrade head        -> {head}")
        _alembic(target, "downgrade", str(previous))
        print(f"downgrade           -> {previous}")
        _alembic(target, "upgrade", "head")
        print(f"re-upgrade head     -> {head}")
        verdict = [ln for ln in _alembic(target, "check").splitlines()
                   if "upgrade operations" in ln]
        print(f"check               -> {verdict[-1] if verdict else 'NO VERDICT'}")
        print(f"OK on {name}")
        return 0
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
        print(f"dropped {name}")


if __name__ == "__main__":
    raise SystemExit(main())
