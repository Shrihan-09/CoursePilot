"""Derived-state cache for deterministic degree audits (Phase 5.7).

> This table is **derived state**. It is never an authority and never a
> source of academic truth.

Everything in it can be reconstructed by running the Degree Engine again.
Deleting every row costs latency and nothing else - and a test asserts
exactly that: after truncating this table the audit is identical.

## What makes a row valid

A row is usable only when **all three** fingerprints still match what the
engine would be given today:

```
academic_fingerprint   the student's own facts
rules_token            the rule state (see app/models/rules_version.py)
engine_version         the engine's semantics and policy identities
```

The fingerprints are hashes of the **actual input rows**, so invalidation is
a property of the data rather than of anyone remembering to call an
invalidation function. If a requirement is recurated, the hash changes, the
stored row no longer matches, and it is simply never read. A missed
invalidation call cannot produce a stale audit because no invalidation call
is required for correctness.

## Why one row per student

`student_id` is the primary key, so a student has at most one cached audit
and a recomputation *replaces* it. That bounds storage to O(students)
without a reaper, and it means a superseded entry cannot linger and be
served by mistake - there is nowhere for it to linger.

The cost is that a student who oscillates between two states recomputes each
time. That is the right trade: correctness is free and the pathological case
is rare.

## Deletion behaviour

`ON DELETE CASCADE` from `student`. This is the opposite of the choices made
for `student_link_event` (RESTRICT) and deliberately so: a link event is
evidence about who was granted access and must outlive everything, whereas a
cached audit is a recomputable artifact that must **not** outlive its
student. Derived state blocking a delete would be absurd.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, LargeBinary, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class StudentAuditCache(Base, TimestampMixin):
    """One cached `DegreeAuditResult` per student, valid only for its inputs."""

    __tablename__ = "student_audit_cache"

    #: The primary key IS the student. One live entry each, replaced on
    #: recomputation - see the module docstring.
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("student.id", ondelete="CASCADE"), primary_key=True
    )

    #: SHA-256 hex digest of the student's own audit inputs.
    academic_fingerprint: Mapped[str] = mapped_column(String(64))

    #: Rule-state identity. NOT always a hash - Phase 5.8 made this a token
    #: that is either the database-maintained rules version (`v:<n>`) or, on
    #: a backend without the trigger, the full fingerprint (`f:<sha256>`).
    #: The prefix keeps the two unambiguous, so a row written under one
    #: mechanism can never be read as if it were the other.
    rules_token: Mapped[str] = mapped_column(String(80))

    engine_version: Mapped[str] = mapped_column(String(64))

    #: zlib-compressed `DegreeAuditResult.model_dump_json()`.
    #:
    #: Measured (Phase 5.8): 24,277 raw bytes insert in ~46 ms on this
    #: PostgreSQL, while 3,487 compressed bytes insert in ~2.2 ms - the cold
    #: path was dominated by payload SIZE, not by column type (a 24 KB bytea
    #: was just as slow as 24 KB of text). Compression costs 0.15 ms and
    #: decompression 0.02 ms, so it is close to free on both paths.
    #:
    #: `bytea` rather than base64 text: base64 would inflate the bytes by a
    #: third to gain nothing, and compressed data is not text.
    #:
    #: The compressed bytes are of the EXACT json a fresh audit produces -
    #: not JSONB, which reorders keys and normalizes numerics. A cache that
    #: returns *equivalent* data is not good enough when the whole claim is
    #: that a hit and a miss are indistinguishable.
    #:
    #: Never pickle - this row crosses processes and deploys, and unpickling
    #: is code execution.
    result_blob: Mapped[bytes] = mapped_column(LargeBinary)

    def __repr__(self) -> str:
        return f"<StudentAuditCache {self.student_id} {self.rules_token}>"
