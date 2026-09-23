"""Rule-state identity for the audit cache (Phase 5.8).

Phase 5.7 answered "have the rules changed?" by hashing every rule row -
correct, and ~14 ms on every audit, hit or miss. This answers the same
question with a single-row read, and gives up nothing, because the version
it reads is maintained by **database triggers** rather than by application
code.

```
v:<n>      the database-maintained rules version   PostgreSQL, ~0.3 ms
f:<sha>    the full reflective fingerprint         anywhere, ~14 ms
```

The prefix matters. Without it a version number and a hash could collide in
principle, and more importantly a row written under one mechanism must never
be read as though it were the other. A mismatch is a cache miss, which is
always safe.

## Why a trigger and not an ORM hook

```
ORM event hooks    fire when the write went through SQLAlchemy
database triggers  fire when the write reached the table
```

Recuration is exactly the case where the difference bites: a catalog fix
applied with `psql`, a bulk `UPDATE` in a migration, an ingestion job that
uses Core rather than the ORM. Phase 5.7 rejected `MAX(updated_at)` for this
reason, and an ORM callback would have the same hole.

## The fallback is not a weaker guarantee

If the version table or its triggers are absent - SQLite, a database not yet
migrated, a permissions problem - this falls back to the full fingerprint,
which is the Phase 5.7 mechanism and is exactly as correct. It is slower,
and it is never wrong. Falling back to "assume unchanged" would be the
unsafe option and is not offered.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

VERSION_PREFIX = "v:"
FINGERPRINT_PREFIX = "f:"


def read_rules_version(session: Session) -> int | None:
    """The database-maintained counter, or None if unavailable.

    Never raises. An absent table, an unmigrated database or a revoked
    permission all mean "use the fingerprint instead", not "fail the audit".
    """
    try:
        return session.execute(
            text("SELECT version FROM rules_version WHERE id = 1")
        ).scalar()
    except Exception:
        logger.debug("rules_version_unavailable", exc_info=True)
        # The failed statement aborts a PostgreSQL transaction, and the
        # fingerprint fallback is about to query the same session.
        from app.services.audit.cache import _safe_rollback

        _safe_rollback(session)
        return None


def rules_token(session: Session, program_version_id: uuid.UUID) -> str:
    """Identify the rule state that binds this student.

    Prefers the trigger-maintained version; falls back to the full Phase 5.7
    fingerprint when that is not available.
    """
    version = read_rules_version(session)
    if version is not None:
        return f"{VERSION_PREFIX}{version}"

    from app.services.audit.cache import rules_fingerprint

    return f"{FINGERPRINT_PREFIX}{rules_fingerprint(session, program_version_id)}"


__all__ = [
    "FINGERPRINT_PREFIX",
    "VERSION_PREFIX",
    "read_rules_version",
    "rules_token",
]
