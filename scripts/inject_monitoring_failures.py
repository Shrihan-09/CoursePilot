"""Phase 6.8 failure injection: break section monitoring on purpose.

Each fault is a source edit; the monitoring suites run - real-data SQLite,
real PostgreSQL (concurrency, advisory lock) and the HTTP API - against the
dedicated `*_test` database; the file is restored whatever happens.
DETECTED = at least one test failed (`-x`: the first failure is enough). A
fault with no observable effect is reported as MISSED, never claimed as caught.

Usage:  python scripts/inject_monitoring_failures.py [fault ...]
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
SERVICE = "backend/app/services/monitoring.py"
MODELS = "backend/app/models/monitoring.py"
AVAIL = "backend/app/services/availability.py"
SCHED = "backend/app/services/scheduling/service.py"
TESTS = ["ingestion/tests/test_section_monitoring.py",
         "ingestion/tests/test_monitoring_postgres.py",
         "backend/tests/test_watch_api.py"]
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test")

FAULTS = {
    "A": ("term scoping: watches matched by bare index, not term-scoped section", [(SERVICE,
          ("            .join(change, and_(change.section_id == watch.section_id,\n"
          "                               change.term_code == watch.term_code))"),
          "            .join(change, change.index_number == watch.index_number)")]),
    "B": ("ownership: any account may deactivate any watch", [(SERVICE,
          "        SectionWatch.id == watch_id, SectionWatch.user_account_id == account_id))",
          "        SectionWatch.id == watch_id))")]),
    "C": ("duplicate watch protection removed (lookup and unique index)", [(SERVICE,
          ("    existing = _active_watch(session, account_id, term_code, index_number)\n"
          "    if existing is not None:\n        return existing, False\n"),
          ""), (MODELS,
          ("              \"index_number\", unique=True, postgresql_where=_ACTIVE, "
          "sqlite_where=_ACTIVE),"),
          ("              \"index_number\", unique=False, postgresql_where=_ACTIVE, "
          "sqlite_where=_ACTIVE),"))]),
    "D": ("closed -> open filter dropped (any -> open is an opening)", [(SERVICE,
          ("             .where(change.from_status == \"closed\", change.to_status == \"open\",\n"
          "                    AvailabilityChangeDispatch.change_id.is_(None))"),
          ("             .where(change.to_status == \"open\",\n"
          "                    AvailabilityChangeDispatch.change_id.is_(None))"))]),
    "E": ("UNKNOWN -> OPEN treated as an opening", [(SERVICE,
          ("             .where(change.from_status == \"closed\", change.to_status == \"open\",\n"
          "                    AvailabilityChangeDispatch.change_id.is_(None))"),
          ("             .where(change.from_status.in_([\"closed\", \"unknown\"]),\n"
          "                    change.to_status == \"open\",\n"
          "                    AvailabilityChangeDispatch.change_id.is_(None))"))]),
    "F": ("inactive watches still matched", [(SERVICE,
          "            .where(change.id.in_(ids), watch.deactivated_at.is_(None),\n",
          "            .where(change.id.in_(ids),\n")]),
    "G": ("event uniqueness removed (plain INSERT, no unique key)", [(SERVICE,
          # 6.9 moved the RETURNING list (the helper can return rows); same fault.
          ("    result = session.execute(insert(table).values(rows).on_conflict_do_nothing(\n"
          "        index_elements=keys).returning(*(returning or table.primary_key.columns)))"),
          ("    result = session.execute(insert(table).values(rows)\n"
          "                             .returning(*(returning or table.primary_key.columns)))")),
          (MODELS,
          ("        UniqueConstraint(\"watch_id\", \"change_id\", "
          "name=\"uq_notification_event_watch_change\"),\n"),
          "")]),
    "H": ("transition linkage: event takes the processing time, not the change's", [(SERVICE,
          "                \"to_status\": c.to_status, \"observed_at\": c.observed_at,",
          "                \"to_status\": c.to_status, \"observed_at\": now,")]),
    "I": ("linked-component context dropped", [(SERVICE,
          "    others = [s for s in slots if s != mine]",
          "    others = []")]),
    "J": ("removed targets never detected", [(SERVICE,
          "    if newest is None or newest.coverage != \"complete\":\n        return set()",
          "    if True:\n        return set()")]),
    "K": ("schedule generation processes openings (read-only broken)", [(SCHED,
          "    if session.new or session.dirty or session.deleted:\n        session.rollback()",
          ("    from app.services.monitoring import process_openings as _process\n"
          "    _process(session, now=datetime.now().astimezone())\n"
          "    if session.new or session.dirty or session.deleted:\n        session.rollback()"))]),
    "L": ("a watch created after an opening receives it", [(SERVICE,
          "                   watch.created_at <= change.observed_at)",
          "                   )")]),
    "M": ("concurrent refreshes not serialized (advisory lock removed)", [(AVAIL,
          ("    if session.get_bind().dialect.name == \"postgresql\":\n"
          "        session.execute(text(\"SELECT pg_advisory_xact_lock(hashtext(:key))\"),"),
          ("    if False:\n"
          "        session.execute(text(\"SELECT pg_advisory_xact_lock(hashtext(:key))\"),"))]),
    "N": ("one INSERT per watcher (not set-based)", [(SERVICE,
          # 6.9: the event insert now also returns rows for the inbox; same fault.
          ("        created = _insert_ignoring_conflicts(\n"
          "            session, NotificationEvent.__table__, rows, [\"watch_id\", \"change_id\"],\n"),
          ("        created = [row for r in rows for row in _insert_ignoring_conflicts(\n"
          "            session, NotificationEvent.__table__, [r], [\"watch_id\", \"change_id\"],\n"))
          , (SERVICE,
          ("            returning=[NotificationEvent.__table__.c.id,\n"
          "                       NotificationEvent.__table__.c.user_account_id])\n"
          "        inserted = len(created)"),
          ("            returning=[NotificationEvent.__table__.c.id,\n"
          "                       NotificationEvent.__table__.c.user_account_id])]\n"
          "        inserted = len(created)"))]),
    "O": ("events listed across accounts", [(SERVICE,
          "        select(NotificationEvent).where(NotificationEvent.user_account_id == account_id)",
          "        select(NotificationEvent)")]),
    "P": ("dispatch marker never written (crash recovery has no progress record)", [(SERVICE,
          ("        _insert_ignoring_conflicts(\n"
          "            session, AvailabilityChangeDispatch.__table__,"),
          ("        (lambda *a: 0)(\n"
          "            session, AvailabilityChangeDispatch.__table__,"))]),
}


def run(letter: str) -> tuple[bool, str]:
    _, edits = FAULTS[letter]
    originals: dict[pathlib.Path, str] = {}
    try:
        for rel, old, new in edits:
            path = ROOT / rel
            originals.setdefault(path, path.read_text(encoding="utf-8"))
            current = path.read_text(encoding="utf-8")
            if current.count(old) != 1:
                return False, f"INJECTION FAILED: pattern not found exactly once in {rel}"
            path.write_text(current.replace(old, new), encoding="utf-8")
        env = {**os.environ, "DATABASE_URL": TEST_DB, "DATABASE_URL_SYNC": TEST_DB,
               "TEST_DATABASE_URL": TEST_DB}
        proc = subprocess.run([PY, "-m", "pytest", *TESTS, "-q", "-x", "-p", "no:cacheprovider",
                               "-rfE"], cwd=ROOT, capture_output=True, text=True, env=env,
                              check=False)
        lines = proc.stdout.strip().splitlines()
        tail = lines[-1] if lines else proc.stderr[-200:]
        failed = sorted({ln.split("::")[-1].split(" ")[0] for ln in lines
                         if ln.startswith(("FAILED", "ERROR"))})
        return proc.returncode != 0, f"{tail} | {', '.join(failed[:6])}"
    finally:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")


def main() -> None:
    for letter in sys.argv[1:] or list(FAULTS):
        detected, detail = run(letter)
        print(f"{letter:3} {'DETECTED' if detected else 'MISSED  '} {FAULTS[letter][0]}\n"
              f"    {detail}", flush=True)


if __name__ == "__main__":
    main()
