"""SOC `coreCodes` per course offering - term-scoped Core certifications (Phase 7.6).

SOC publishes, inside each course object of a term's `courses.json`, the Core
codes that course carries FOR THAT TERM - every school's codes together:

    {"coreCode": "HST", "effective": "20269", "unit": "01", ...}      SAS Core
    {"coreCode": "SOEHS", "effective": "20269", ...}                  SOE list

`requirement_course_option` (Phase 4) holds the SAS Core certifications the
Core pipeline loaded from ONE archived payload, with no term. That is enough
for the Degree Engine, but not for planning: a course taken in Spring 2027
counts toward a Core goal only if Spring 2027's listing certifies it. This
table keeps every term's codes as published, keyed on the offering, so the
planner and the section data view (app.services.sources.sections) can say
which term's SOC listing certifies a goal.

Codes are stored exactly as SOC writes them. Nothing here decides which
school's Core a code belongs to: a school Core set's goal codes do
(`sas-core.json`). Loaded by the course pipeline from the same archived
payload, and backfilled from the archives by scripts/backfill_core_codes.py.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class OfferingCoreCode(Base, TimestampMixin):
    __tablename__ = "offering_core_code"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    offering_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_offering.id", ondelete="CASCADE"), index=True)
    #: Denormalized from the offering for term-scoped reads (one query per term).
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course.id", ondelete="CASCADE"), index=True)
    term_code: Mapped[str] = mapped_column(String(16))
    #: SOC `coreCode`, verbatim ("AHp", "QQ", "SOEHS").
    code: Mapped[str] = mapped_column(String(16))
    #: SOC `coreCodeDescription`.
    description: Mapped[str | None] = mapped_column(String(255))
    #: SOC `effective` - the term the certification took effect.
    effective: Mapped[str | None] = mapped_column(String(16))
    #: SOC `unit` - the offering unit that published the code.
    unit_code: Mapped[str | None] = mapped_column(String(8))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_source.id"))

    __table_args__ = (
        UniqueConstraint("offering_id", "code", name="uq_offering_core_code"),
        Index("ix_offering_core_code_term_course", "term_code", "course_id"),
    )

    def __repr__(self) -> str:
        return f"<OfferingCoreCode {self.term_code} {self.code}>"


__all__ = ["OfferingCoreCode"]
