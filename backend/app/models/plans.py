"""A student's saved semester plan (Phase 7.2).

> This table is **derived state**. It is never an authority and never a
> source of academic truth.

A plan is a deterministic function of (academic record, program version,
program rules, Rutgers SOC data, start term, constraints, engine versions).
Phase 6.5 deliberately stored nothing because nothing consumed a stored
plan. Phase 7.2 has two consumers that need one: the Plan page must survive
navigation (a cold plan takes up to ~7.6 s p95), and a planned semester is
handed to the Schedule page by reference rather than copied into the browser
as a second, unvalidated source of truth.

## Freshness is computed, never assumed

Each row keeps the fingerprints of every input the plan was computed from
(`academic_fingerprint`, `rules_fingerprint`, `offering_dataset`, the engine
versions). Reading a plan recomputes them and reports *which* input changed
(app.services.planning.saved). A changed fingerprint makes the plan STALE -
it is still shown, labelled as such, and never presented as current. As with
the audit cache, correctness depends on no invalidation call being made.

## One row per student

`student_id` is unique: regenerating REPLACES the plan. There is no plan
history product in this phase, and a superseded plan cannot linger and be
read by mistake. What-if plans (another program) are never saved.

## Deletion behaviour

`ON DELETE CASCADE` from `student` and from `program_version`: a recomputable
artifact must not outlive, or block the deletion of, what it was derived from.

The result is stored as zlib-compressed JSON of the EXACT `PlanResult` the
engine produced (the audit-cache precedent: not JSONB, which reorders keys and
normalizes numerics; never pickle).
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, ForeignKey, LargeBinary, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class StudentPlan(Base, TimestampMixin):
    """The student's current saved plan and the inputs it was computed from."""

    __tablename__ = "student_plan"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("student.id", ondelete="CASCADE"), unique=True)
    program_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("program_version.id", ondelete="CASCADE"))
    catalog_year: Mapped[str] = mapped_column(String(16))

    # The request snapshot - with the fingerprints below, enough to
    # reproduce the plan byte for byte.
    start_term: Mapped[str] = mapped_column(String(5))
    constraints: Mapped[dict] = mapped_column(JSON)

    planning_engine_version: Mapped[str] = mapped_column(String(32))
    audit_engine_version: Mapped[str] = mapped_column(String(64))
    academic_fingerprint: Mapped[str] = mapped_column(String(64))
    rules_fingerprint: Mapped[str] = mapped_column(String(64))
    offering_dataset: Mapped[list] = mapped_column(JSON)

    plan_status: Mapped[str] = mapped_column(String(32))
    result_blob: Mapped[bytes] = mapped_column(LargeBinary)

    def __repr__(self) -> str:
        return f"<StudentPlan {self.student_id} {self.start_term} {self.plan_status}>"


__all__ = ["StudentPlan"]
