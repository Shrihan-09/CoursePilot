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
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, LargeBinary, String, Text, Uuid
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

    #: Prompt 11: bumped on every write - a preview applies only to the
    #: revision it was computed from.
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    #: Prompt 11: the student's own changes (app.domain.plan_edits.PlanEdits),
    #: carried into every regeneration of this plan.
    edits: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")

    def __repr__(self) -> str:
        return f"<StudentPlan {self.student_id} {self.start_term} {self.plan_status}>"


#: Columns a history snapshot copies from `student_plan` (and restores on undo).
SNAPSHOT_COLUMNS = ("program_version_id", "catalog_year", "start_term", "constraints",
                    "planning_engine_version", "audit_engine_version", "academic_fingerprint",
                    "rules_fingerprint", "offering_dataset", "plan_status", "result_blob",
                    "edits")


class StudentPlanHistory(Base, TimestampMixin):
    """Prompt 11 A2: the last few versions of the saved plan, for "Undo last
    change". Derived state like the plan itself; kept to HISTORY_DEPTH rows per
    student and deleted with the student."""

    __tablename__ = "student_plan_history"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("student.id", ondelete="CASCADE"), index=True)
    #: The plan's revision this snapshot WAS.
    revision: Mapped[int] = mapped_column(Integer)
    #: What the student changed next (shown as "Undo: <summary>").
    change_summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    program_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("program_version.id", ondelete="CASCADE"))
    catalog_year: Mapped[str] = mapped_column(String(16))
    start_term: Mapped[str] = mapped_column(String(5))
    constraints: Mapped[dict] = mapped_column(JSON)
    planning_engine_version: Mapped[str] = mapped_column(String(32))
    audit_engine_version: Mapped[str] = mapped_column(String(64))
    academic_fingerprint: Mapped[str] = mapped_column(String(64))
    rules_fingerprint: Mapped[str] = mapped_column(String(64))
    offering_dataset: Mapped[list] = mapped_column(JSON)
    plan_status: Mapped[str] = mapped_column(String(32))
    result_blob: Mapped[bytes] = mapped_column(LargeBinary)
    edits: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")


class PlanChangePreview(Base, TimestampMixin):
    """Prompt 11 A2/A4: a PROPOSED change, computed by the planner and never
    the plan. `apply` copies it into `student_plan` only while the plan is
    still at `base_revision` and the preview has not expired."""

    __tablename__ = "plan_change_preview"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("student.id", ondelete="CASCADE"), index=True)
    base_revision: Mapped[int] = mapped_column(Integer)
    change: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    edits: Mapped[dict] = mapped_column(JSON)
    program_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("program_version.id", ondelete="CASCADE"))
    catalog_year: Mapped[str] = mapped_column(String(16))
    constraints: Mapped[dict] = mapped_column(JSON)
    planning_engine_version: Mapped[str] = mapped_column(String(32))
    audit_engine_version: Mapped[str] = mapped_column(String(64))
    academic_fingerprint: Mapped[str] = mapped_column(String(64))
    rules_fingerprint: Mapped[str] = mapped_column(String(64))
    offering_dataset: Mapped[list] = mapped_column(JSON)
    plan_status: Mapped[str] = mapped_column(String(32))
    result_blob: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


__all__ = ["PlanChangePreview", "SNAPSHOT_COLUMNS", "StudentPlan", "StudentPlanHistory"]
