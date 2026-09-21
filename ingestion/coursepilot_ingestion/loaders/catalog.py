"""Loader: normalized catalog entries -> `catalog_course_entry` rows.

Three rules it enforces, all about not corrupting Phase 1 data:

1. **Never create a Course.** Catalog entries are matched against existing
   `course` rows by the Phase 1 natural key. A fabricated course would become
   eligible for requirements and satisfiable by nothing real.

2. **Never drop an unmatched entry either.** Measured: 13 of 43 CS catalog
   courses have no `course` row, because the catalog is a SUPERSET of what SOC
   offers in any given term. Those are stored with `course_id = NULL` and
   reported, and the link is backfilled if SOC later offers the course.

3. **Never overwrite SOC credits.** `course.credits` is the term-scoped SOC
   value; catalog credits (which can be ranges) live on
   `catalog_course_entry.credits_min/max`. Writing one onto the other would
   destroy the distinction the schema exists to preserve.

Idempotent: keyed on (course_string, catalog_year), so re-running updates in
place.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.models import CatalogCourseEntry, Course, DataSource
from sqlalchemy import select
from sqlalchemy.orm import Session

from coursepilot_ingestion.catalog_schemas import (
    CatalogIngestionStats,
    NormalizedCatalogCourse,
)

logger = logging.getLogger(__name__)

# Columns refreshed when an entry is seen again. Excludes the natural key.
_MUTABLE_FIELDS = (
    "title",
    "description",
    "credits_min",
    "credits_max",
    "credits_raw",
    "source_url",
)


class CatalogLoader:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ #
    # provenance
    # ------------------------------------------------------------------ #

    def get_or_create_source(
        self,
        *,
        kind: str,
        url: str,
        content_hash: str,
        retrieved_at: datetime,
        catalog_year: str,
        archive_path: str | None,
        record_count: int | None,
    ) -> DataSource:
        """Reuse the source row when the page is byte-identical.

        Keyed on content hash, so re-ingesting unchanged catalog data does not
        churn provenance, while a genuinely changed page always gets a new row.
        """
        existing = self.session.scalar(
            select(DataSource).where(
                DataSource.content_hash == content_hash,
                DataSource.academic_year == catalog_year,
            )
        )
        if existing is not None:
            logger.info("reusing DataSource %s (unchanged catalog page)", existing.id)
            return existing

        source = DataSource(
            kind=kind,
            url=url,
            title=f"Rutgers catalog {catalog_year}",
            retrieved_at=retrieved_at,
            content_hash=content_hash,
            raw_payload_ref=archive_path,
            academic_year=catalog_year,
            record_count=record_count,
        )
        self.session.add(source)
        self.session.flush()
        logger.info("created DataSource %s", source.id)
        return source

    # ------------------------------------------------------------------ #
    # course resolution
    # ------------------------------------------------------------------ #

    def _resolve_course(self, course_string: str) -> Course | None:
        """Map a catalog course code onto an existing Course.

        supplement_code is '' because the catalog never publishes supplements.
        A lecture/lab pair would need the supplement spelled out, and failing
        to resolve is safer than arbitrarily picking one of the two.
        """
        try:
            unit, subject, number = course_string.split(":")
        except ValueError:
            return None
        return self.session.scalar(
            select(Course).where(
                Course.offering_unit_code == unit,
                Course.subject_code == subject,
                Course.course_number == number,
                Course.supplement_code == "",
            )
        )

    # ------------------------------------------------------------------ #
    # load
    # ------------------------------------------------------------------ #

    def load(
        self,
        entries: list[NormalizedCatalogCourse],
        source: DataSource,
        stats: CatalogIngestionStats,
    ) -> CatalogIngestionStats:
        for entry in entries:
            course = self._resolve_course(entry.course_string)
            if course is None:
                # The catalog is a superset of what SOC offers in any term, so
                # an unmapped entry is normal, not an error. It is recorded
                # (with course_id NULL) and reported - never fabricated as a
                # Course, and never silently dropped.
                stats.unmapped.append(entry.course_string)

            existing = self.session.scalar(
                select(CatalogCourseEntry).where(
                    CatalogCourseEntry.course_string == entry.course_string,
                    CatalogCourseEntry.catalog_year == entry.catalog_year,
                )
            )

            if existing is not None:
                changed = False
                for name in _MUTABLE_FIELDS:
                    new_value = getattr(entry, name, None)
                    # Never overwrite a real value with None: a later page that
                    # omits a field must not erase what an earlier one supplied.
                    if new_value is None:
                        continue
                    if getattr(existing, name) != new_value:
                        setattr(existing, name, new_value)
                        changed = True
                # Backfill the link if SOC has since started offering the course.
                if existing.course_id is None and course is not None:
                    existing.course_id = course.id
                    changed = True
                existing.source_id = source.id
                if changed:
                    stats.entries_updated += 1
            else:
                self.session.add(
                    CatalogCourseEntry(
                        course_string=entry.course_string,
                        course_id=course.id if course is not None else None,
                        catalog_year=entry.catalog_year,
                        title=entry.title,
                        description=entry.description,
                        credits_min=entry.credits_min,
                        credits_max=entry.credits_max,
                        credits_raw=entry.credits_raw,
                        source_url=entry.source_url,
                        source_id=source.id,
                    )
                )
                stats.entries_inserted += 1

            if entry.description:
                stats.descriptions_populated += 1
            if entry.is_credit_range:
                stats.credit_ranges += 1

        self.session.flush()

        if stats.unmapped:
            logger.info(
                "%d catalog course(s) have no SOC course row (stored unlinked): %s",
                len(stats.unmapped),
                stats.unmapped[:10],
            )
        return stats
