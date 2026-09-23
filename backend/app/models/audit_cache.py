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
rules_fingerprint      the program version, requirements, eligibility, rules
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

from sqlalchemy import ForeignKey, String, Text, Uuid
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

    #: SHA-256 hex digests of the audit inputs. Stored rather than compared
    #: in SQL so a stale row is visible to an operator debugging a miss.
    academic_fingerprint: Mapped[str] = mapped_column(String(64))
    rules_fingerprint: Mapped[str] = mapped_column(String(64))
    engine_version: Mapped[str] = mapped_column(String(64))

    #: `DegreeAuditResult.model_dump_json()`. Text, not JSON/JSONB, on
    #: purpose: JSONB reorders keys and normalizes numerics, which would mean
    #: the bytes handed back to a client differ from the bytes a fresh audit
    #: produced. A cache that returns *equivalent* data is not good enough
    #: when the whole claim is that a hit and a miss are indistinguishable.
    #:
    #: Never pickle - this row crosses processes and deploys, and unpickling
    #: is code execution.
    result_json: Mapped[str] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<StudentAuditCache {self.student_id} {self.engine_version}>"
