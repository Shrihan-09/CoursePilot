"""The cached audit entry point (Phase 5.7).

```
Student academic facts
        |
        v
Degree Engine            <- sole authority, unchanged
        |
        v
DegreeAuditResult
        |
        v
Audit Cache              <- derived state, never an authority
        |
        v
Student Audit API
```

This is a **wrapper**, not a variant of the engine. `DegreeAuditEngine` does
not know the cache exists, was not modified to accommodate it, and remains
callable directly - which is what lets a test assert that a cache hit and a
fresh computation are byte-identical.

The cache decides one thing only: whether to call the engine. It never
decides requirement satisfaction, credits, eligibility, allocation or
anything else academic, and it cannot - it holds opaque serialized bytes.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.domain.audit import DegreeAuditResult
from app.models import Student
from app.services.audit.cache import (
    AuditCacheKey,
    _safe_rollback,
    read_cached_audit,
    write_cached_audit,
)
from app.services.audit.engine import DegreeAuditEngine

logger = logging.getLogger(__name__)


def audit_with_cache(
    session: Session, student: Student, *, use_cache: bool = True
) -> tuple[DegreeAuditResult, bool]:
    """Return `(result, from_cache)`.

    The boolean is for observability and tests. It is **not** returned to
    clients: Phase 5.6's contract is that this endpoint yields a
    `DegreeAuditResult`, and whether a cache was involved is an
    implementation detail that has no business in an academic domain object.

    `use_cache=False` forces a fresh computation - used by tests that need to
    compare, never by a request, because there is no client input that could
    reach it.
    """
    if not use_cache:
        return DegreeAuditEngine(session).audit(student), False

    try:
        key = AuditCacheKey.compute(session, student)
    except Exception:
        # Fingerprinting failed. Without a trustworthy key nothing may be
        # served from cache, so fall through to the engine - the audit is
        # still correct, it is merely uncached.
        #
        # Rolled back first: a database error aborts the transaction, and the
        # engine is about to run on this very session.
        logger.warning("audit_cache_key_failed", exc_info=True)
        _safe_rollback(session)
        return DegreeAuditEngine(session).audit(student), False

    cached = read_cached_audit(session, student, key)
    if cached is not None:
        logger.info("audit_cache_hit")
        return cached, True

    logger.info("audit_cache_miss")
    result = DegreeAuditEngine(session).audit(student)
    # A failed write is logged inside and changes nothing for this caller:
    # they already hold a correct, freshly computed audit.
    write_cached_audit(session, student, key, result)
    return result, False


__all__ = ["audit_with_cache"]
