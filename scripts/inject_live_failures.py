"""Phase 6.9 failure injection: break CoursePilot Live on purpose, prove the tests notice.

Each fault is a source edit; the Live suites run - offline domain/adapter,
real-data SQLite, real PostgreSQL concurrency, HTTP API - against the
dedicated `*_test` database; the file is restored whatever happens.
DETECTED = at least one test failed (`-x`). A fault with no observable
effect is reported as MISSED, never claimed as caught.

Usage:  python scripts/inject_live_failures.py [fault ...]
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
LIVE = "backend/app/services/live.py"
DOMAIN = "backend/app/domain/live.py"
MODELS = "backend/app/models/live.py"
PROVIDERS = "backend/app/services/push_providers.py"
WORKER = "ingestion/coursepilot_ingestion/live_worker.py"
TESTS = ["backend/tests/test_live_domain.py", "ingestion/tests/test_coursepilot_live.py",
         "ingestion/tests/test_live_postgres.py", "backend/tests/test_live_api.py"]
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test")

FAULTS = {
    "A": ("scheduler runs twice: the term lease is ignored", [(LIVE,
          ("        or_(AvailabilityPollSchedule.lease_until.is_(None),\n"
          "            AvailabilityPollSchedule.lease_until < now))"),
          "        True)")]),
    # Both guards: the schedule list AND the lease claim refuse inactive terms.
    "B": ("term scope removed: inactive terms are polled", [(LIVE,
          "    if active_only:\n        q = q.where(AvailabilityPollSchedule.active.is_(True))",
          "    if False:\n        q = q.where(AvailabilityPollSchedule.active.is_(True))"), (LIVE,
          "        AvailabilityPollSchedule.term_code == term_code, "
          "AvailabilityPollSchedule.active.is_(True),",
          "        AvailabilityPollSchedule.term_code == term_code,")]),
    "C": ("automatic monitoring processing skipped", [(WORKER,
          ("        opened = self._step(report, \"process_openings\",\n"
          "                            lambda s: process_openings(s, now=self.clock()))"),
          "        opened = None")]),
    "D": ("delivery uniqueness removed", [(MODELS,
          ("        UniqueConstraint(\"event_id\", \"endpoint_id\", \"channel\",\n"
          "                         name=\"uq_notification_delivery_event_endpoint_channel\"),\n"),
          ""), (LIVE,
          ("            session, NotificationDelivery.__table__, rows, "
          "[\"event_id\", \"endpoint_id\", \"channel\"],"),
          "            session, NotificationDelivery.__table__, rows, [\"id\"],")]),
    "E": ("ownership check removed: any account may revoke any endpoint", [(LIVE,
          ("        DeliveryEndpoint.id == endpoint_id, DeliveryEndpoint.user_account_id == "
          "account_id))"),
          "        DeliveryEndpoint.id == endpoint_id))")]),
    "F": ("an inactive watch still sends externally", [(LIVE,
          ("    if watch is None or watch.deactivated_at is not None:\n"
          "        return SuppressedReason.WATCH_INACTIVE"),
          "    if False:\n        return SuppressedReason.WATCH_INACTIVE")]),
    "G": ("an invalid APNs token is retried forever", [(DOMAIN,
          ("        return SendResult(Outcome.ENDPOINT_INVALID, ErrorClass.ENDPOINT_INVALID, "
          "status, reason)"),
          "        return SendResult(Outcome.RETRY, ErrorClass.ENDPOINT_INVALID, status, reason)")]),
    "H": ("a transient provider error is marked permanent", [(DOMAIN,
          ("    if status == 503:\n        return SendResult(Outcome.RETRY, "
          "ErrorClass.PROVIDER_UNAVAILABLE"),
          ("    if status == 503:\n        return SendResult(Outcome.PERMANENT, "
          "ErrorClass.PROVIDER_UNAVAILABLE"))]),
    "I": ("a permanent rejection is retried", [(LIVE,
          "    elif result.outcome is Outcome.PERMANENT:", "    elif False:")]),
    "J": ("a crashed claim is never recovered (expired leases ignored)", [(LIVE,
          ("        and_(NotificationDelivery.status == \"claimed\", "
          "NotificationDelivery.claimed_until < now)))"),
          "        and_(NotificationDelivery.status == \"claimed\", False)))")]),
    "K": ("a stale worker's result is not fenced (retry duplicates/overwrites)", [(LIVE,
          ("        NotificationDelivery.id == delivery_id, NotificationDelivery.claim_token == token,\n"
          "        NotificationDelivery.status == \"claimed\")"),
          "        NotificationDelivery.id == delivery_id)")]),
    "L": ("Account B can attach a Live Activity to Account A's watch", [(LIVE,
          "        SectionWatch.id == watch_id, SectionWatch.user_account_id == account_id))",
          "        SectionWatch.id == watch_id))")]),
    "M": ("an ActivityKit/APNs token appears in the logs", [(PROVIDERS,
          "    logging.getLogger(_name).setLevel(logging.WARNING)",
          "    logging.getLogger(_name).setLevel(logging.DEBUG)")]),
    "N": ("reading a notification mutates the factual event", [(LIVE,
          "        item.read_at = now",
          ("        item.read_at = now\n"
          "        session.get(NotificationEvent, item.event_id).updated_at = now"))]),
    "O": ("fan-out polls Rutgers once per watch", [(WORKER,
          "                raw = pipeline.fetch(query)                       # no transaction open",
          ("                raw = [pipeline.fetch(query) for _w in s.execute(__import__("
          "'sqlalchemy').text('SELECT 1 FROM section_watch WHERE deactivated_at IS NULL'))"
          ".all() or [0]][-1]"))]),
    "P": ("a scheduler failure is silently swallowed", [(WORKER,
          "            outcome = f\"error:{type(exc).__name__}\"",
          "            outcome = \"accepted\"\n            return None")]),
    "Q": ("ActivityKit token rotation is ignored", [(LIVE,
          ("    new = _add_endpoint(session, account_id, EndpointKind.APNS_LIVE_ACTIVITY.value,\n"
          "                        environment, push_token, now)\n    if old is not None:"),
          ("    return activity\n"
          "    new = _add_endpoint(session, account_id, EndpointKind.APNS_LIVE_ACTIVITY.value,\n"
          "                        environment, push_token, now)\n    if old is not None:"))]),
    "R": ("a stale (expired/ended) activity token stays active", [(LIVE,
          ("                _revoke(endpoint, \"expired\" if state == \"expired\" else "
          "\"activity_ended\", now)"),
          "                pass")]),
    "S": ("an unknown linked component is fabricated in the payload", [(DOMAIN,
          "    if f.component_state is not None:", "    if True:")]),
    "T": ("an unrelated course gets lab/component data", [(LIVE,
          "component_state=event.required_component_state,",
          "component_state=event.required_component_state or \"closed\",")]),
    # discovered while implementing
    "U": ("an hours-old opening is still pushed (event TTL ignored)", [(LIVE,
          "    if now > _utc(event.observed_at) + EVENT_TTL:\n        return SuppressedReason.EVENT_EXPIRED",
          "    if False:\n        return SuppressedReason.EVENT_EXPIRED")]),
    "V": ("a disabled account is still pushed", [(LIVE,
          "    if account is None or account.disabled_at is not None:",
          "    if account is None:")]),
    "W": ("double alert: normal push sent beside the Live Activity (Apple HIG)", [(LIVE,
          "                reason = SuppressedReason.LIVE_ACTIVITY_ALERTED if live else None",
          "                reason = None")]),
    "X": ("lease fence removed: a worker that lost its lease still writes", [(LIVE,
          "    return session.scalar(q) is not None", "    return True")]),
    "Y": ("attempts not counted at claim (unbounded retries)", [(LIVE,
          "attempts=NotificationDelivery.attempts + 1, last_attempt_at=now,",
          "attempts=NotificationDelivery.attempts, last_attempt_at=now,")]),
    "Z": ("concurrent workers claim the same delivery (SKIP LOCKED removed)", [(LIVE,
          "        due = due.with_for_update(skip_locked=True)\n    ids = session.scalars(due).all()",
          "        pass\n    ids = session.scalars(due).all()")]),
    "AA": ("provider exception text (with the token URL) is kept", [(PROVIDERS,
           ("        except httpx.HTTPError:\n"
           "            # The exception text contains the URL - and so the token. Dropped.\n"
           "            return SendResult(Outcome.RETRY, ErrorClass.NETWORK)"),
           ("        except httpx.HTTPError as exc:\n"
           "            return SendResult(Outcome.RETRY, ErrorClass.NETWORK,\n"
           "                              provider_reason=str(exc)[:200])"))]),
    "AB": ("the inbox item is not created with the event", [(
           "backend/app/services/monitoring.py",
           "        create_inbox_items(session, [(e, a) for e, a in created], now)",
           "        pass")]),
    "AC": ("watch cancellation leaves its Live Activity running", [(
           "backend/app/services/monitoring.py",
           "        end_activities_for_watches(session, [watch.id], now=now)       # Phase 6.9",
           "        pass")]),
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
                              check=False, timeout=1800)
        lines = proc.stdout.strip().splitlines()
        tail = lines[-1] if lines else proc.stderr[-200:]
        failed = sorted({ln.split("::")[-1].split(" ")[0] for ln in lines
                         if ln.startswith(("FAILED", "ERROR"))})
        return proc.returncode != 0, f"{tail} | {', '.join(failed[:6])}"
    except subprocess.TimeoutExpired:
        return True, "TIMEOUT (a hang is a detected failure; the run was stopped)"
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
