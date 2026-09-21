"""Loader: validated courses in, database rows out.

## Idempotency

Re-running ingestion must not create duplicate courses. Two mechanisms, and
both matter:

1. **The UNIQUE constraint** on the course natural key. This is the real
   guarantee - it is enforced by the database and cannot be bypassed by a bug
   in this file.

2. **Look up, then insert or update.** Application-level, and what makes the
   re-run produce a sensible result rather than an integrity error.

Belt and braces on purpose: application logic decides what *should* happen,
the constraint guarantees what *can* happen.

## Why not INSERT ... ON CONFLICT?

Postgres's native upsert is faster and race-free, and it is the right answer
if ingestion ever runs concurrently. It is deliberately not used yet because
it is dialect-specific, and the portable version lets this exact code be
tested without a running Postgres. Ingestion is currently a single-threaded
batch job, so the race window does not arise. Revisit when that changes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from app.models import Course, CourseOffering, DataSource, Subject
from sqlalchemy import select
from sqlalchemy.orm import Session

from coursepilot_ingestion.schemas import IngestionStats, NormalizedCourse

logger = logging.getLogger(__name__)

# Course columns refreshed when a course is seen again. Deliberately excludes
# the natural-key columns: if those changed it would be a different course,
# and silently rewriting them would corrupt identity.
_MUTABLE_COURSE_FIELDS = (
    "title",
    "title_abbrev",
    "description",
    "credits",
    "credits_description",
    "level",
    "school_code",
    "school_description",
    "prereq_notes_raw",
    "synopsis_url",
    "course_string",
)


class CourseLoader:
    """Writes normalized courses into the database."""

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
        term_code: str,
        academic_year: str,
        archive_path: str | None,
        record_count: int | None,
    ) -> DataSource:
        """Reuse the source row when the payload is byte-identical.

        Keying on the content hash means re-running against unchanged data
        does not pile up near-duplicate provenance rows, while a genuinely
        changed payload always produces a new one. That is what makes
        "when did this fact change?" answerable later.
        """
        existing = self.session.scalar(
            select(DataSource).where(
                DataSource.content_hash == content_hash,
                DataSource.term_code == term_code,
            )
        )
        if existing is not None:
            logger.info("reusing DataSource %s (unchanged payload)", existing.id)
            return existing

        source = DataSource(
            kind=kind,
            url=url,
            title=f"Rutgers SOC courses {term_code}",
            retrieved_at=retrieved_at,
            content_hash=content_hash,
            raw_payload_ref=archive_path,
            academic_year=academic_year,
            term_code=term_code,
            record_count=record_count,
        )
        self.session.add(source)
        self.session.flush()
        logger.info("created DataSource %s", source.id)
        return source

    # ------------------------------------------------------------------ #
    # subjects
    # ------------------------------------------------------------------ #

    def _get_or_create_subject(
        self, course: NormalizedCourse, source_id: uuid.UUID, stats: IngestionStats
    ) -> Subject:
        subject = self.session.scalar(
            select(Subject).where(
                Subject.offering_unit_code == course.offering_unit_code,
                Subject.code == course.subject_code,
            )
        )
        if subject is not None:
            # Backfill a description that was missing on an earlier run.
            if not subject.description and course.subject_description:
                subject.description = course.subject_description
            return subject

        subject = Subject(
            code=course.subject_code,
            description=course.subject_description,
            offering_unit_code=course.offering_unit_code,
            source_id=source_id,
        )
        self.session.add(subject)
        self.session.flush()
        stats.subjects_inserted += 1
        return subject

    # ------------------------------------------------------------------ #
    # courses + offerings
    # ------------------------------------------------------------------ #

    def _upsert_course(
        self, normalized: NormalizedCourse, subject: Subject, source_id: uuid.UUID, stats: IngestionStats
    ) -> Course:
        unit, subj, number, supplement = normalized.natural_key

        existing = self.session.scalar(
            select(Course).where(
                Course.offering_unit_code == unit,
                Course.subject_code == subj,
                Course.course_number == number,
                Course.supplement_code == supplement,
            )
        )

        if existing is not None:
            changed = False
            for field_name in _MUTABLE_COURSE_FIELDS:
                new_value = getattr(normalized, field_name, None)
                # Never overwrite a real value with None. A later payload that
                # omits a field should not erase data an earlier one supplied.
                if new_value is None:
                    continue
                if getattr(existing, field_name) != new_value:
                    setattr(existing, field_name, new_value)
                    changed = True
            existing.source_id = source_id
            if changed:
                stats.courses_updated += 1
            return existing

        course = Course(
            offering_unit_code=unit,
            subject_code=subj,
            course_number=number,
            supplement_code=supplement,
            course_string=normalized.course_string,
            title=normalized.title,
            title_abbrev=normalized.title_abbrev,
            description=normalized.description,
            credits=normalized.credits,
            credits_description=normalized.credits_description,
            level=normalized.level,
            school_code=normalized.school_code,
            school_description=normalized.school_description,
            prereq_notes_raw=normalized.prereq_notes_raw,
            synopsis_url=normalized.synopsis_url,
            subject_id=subject.id,
            source_id=source_id,
        )
        self.session.add(course)
        self.session.flush()
        stats.courses_inserted += 1
        return course

    def _upsert_offering(
        self, normalized: NormalizedCourse, course: Course, source_id: uuid.UUID, stats: IngestionStats
    ) -> None:
        existing = self.session.scalar(
            select(CourseOffering).where(
                CourseOffering.course_id == course.id,
                CourseOffering.term_code == normalized.term_code,
                CourseOffering.campus_code == normalized.campus_code,
            )
        )
        if existing is not None:
            existing.open_sections_observed = normalized.open_sections_observed
            existing.section_count_observed = normalized.section_count_observed
            existing.source_id = source_id
            stats.offerings_updated += 1
            return

        self.session.add(
            CourseOffering(
                course_id=course.id,
                term_code=normalized.term_code,
                campus_code=normalized.campus_code,
                open_sections_observed=normalized.open_sections_observed,
                section_count_observed=normalized.section_count_observed,
                source_id=source_id,
            )
        )
        stats.offerings_inserted += 1

    def load(
        self, courses: list[NormalizedCourse], source: DataSource, stats: IngestionStats
    ) -> IngestionStats:
        """Load a batch. The caller owns the transaction boundary.

        Note the ordering: a course that appears twice for two campuses is
        upserted once and gains two offerings, which is exactly the NB/OB case
        measured in the real payload.
        """
        for normalized in courses:
            subject = self._get_or_create_subject(normalized, source.id, stats)
            course = self._upsert_course(normalized, subject, source.id, stats)
            self._upsert_offering(normalized, course, source.id, stats)

        self.session.flush()
        return stats
