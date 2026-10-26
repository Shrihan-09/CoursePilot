"""Loader: SOC `coreCodes` -> term-scoped `offering_core_code` rows (Phase 7.6).

Runs inside the course pipeline after courses and offerings are loaded, on the
SAME archived payload, so every code carries the exact SOC source row of the
term it was published in. One row per (offering, code); re-running is
idempotent: an unchanged code is left alone, a changed description/effective
term is updated, a code the payload no longer lists is removed. Other terms'
offerings are never touched.

A coreCodes entry without a code is reported (`skipped_malformed`), never
guessed.
"""

from __future__ import annotations

from app.models import Course, CourseOffering, DataSource
from app.models.core_codes import OfferingCoreCode
from sqlalchemy import select
from sqlalchemy.orm import Session

from coursepilot_ingestion.loaders.prerequisites import _raw_key
from coursepilot_ingestion.schemas import IngestionStats, NormalizedCourse, RawSocCourse


def _text(value) -> str | None:
    value = (str(value).strip() if value is not None else "")
    return value or None


def entries_of(raw: RawSocCourse) -> tuple[dict[str, dict], int]:
    """(code -> fields, malformed count) for one SOC course record."""
    out: dict[str, dict] = {}
    malformed = 0
    for entry in raw.coreCodes or []:
        if not isinstance(entry, dict):
            malformed += 1
            continue
        code = _text(entry.get("coreCode") or entry.get("code"))
        if code is None or len(code) > 16:
            malformed += 1
            continue
        out[code] = {"description": _text(entry.get("coreCodeDescription")
                                          or entry.get("description")),
                     "effective": _text(entry.get("effective")),
                     "unit_code": _text(entry.get("unit") or entry.get("offeringUnitCode"))}
    return out, malformed


class CoreCodeLoader:
    def __init__(self, session: Session) -> None:
        self.session = session

    def load(self, raws: list[RawSocCourse], courses: list[NormalizedCourse],
             source: DataSource, stats: IngestionStats) -> IngestionStats:
        counts = stats.core_codes
        raw_by_key = {_raw_key(r): r for r in raws}
        term_codes = {c.term_code for c in courses}
        offerings = {
            (c.offering_unit_code, c.subject_code, c.course_number, c.supplement_code,
             o.campus_code): o
            for o, c in self.session.execute(
                select(CourseOffering, Course)
                .join(Course, Course.id == CourseOffering.course_id)
                .where(CourseOffering.term_code.in_(term_codes))).all()}
        existing: dict = {}
        for row in self.session.scalars(select(OfferingCoreCode)
                                        .where(OfferingCoreCode.term_code.in_(term_codes))):
            existing.setdefault(row.offering_id, {})[row.code] = row
        for normalized in courses:
            key = (*normalized.natural_key, normalized.campus_code)
            raw, offering = raw_by_key.get(key), offerings.get(key)
            if raw is None or offering is None:
                continue
            wanted, malformed = entries_of(raw)
            if malformed:
                counts["skipped_malformed"] = counts.get("skipped_malformed", 0) + malformed
            have = existing.get(offering.id, {})
            for code in sorted(set(have) - set(wanted)):
                self.session.delete(have[code])
                counts["deleted"] = counts.get("deleted", 0) + 1
            for code, fields in sorted(wanted.items()):
                row = have.get(code)
                if row is None:
                    self.session.add(OfferingCoreCode(
                        offering_id=offering.id, course_id=offering.course_id,
                        term_code=offering.term_code, code=code, source_id=source.id, **fields))
                    counts["inserted"] = counts.get("inserted", 0) + 1
                elif any(getattr(row, k) != v for k, v in fields.items()):
                    for k, v in fields.items():
                        setattr(row, k, v)
                    row.source_id = source.id
                    counts["updated"] = counts.get("updated", 0) + 1
                else:
                    counts["unchanged"] = counts.get("unchanged", 0) + 1
        self.session.flush()
        return stats


__all__ = ["CoreCodeLoader", "entries_of"]
