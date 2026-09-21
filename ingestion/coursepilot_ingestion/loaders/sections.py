"""Loader: validated sections in, database rows out.

## Course association

A section is attached to a `course_offering`, resolved by the parent course's
**natural key** plus term and campus:

    (offering_unit_code, subject_code, course_number, supplement_code)
      + term_code + campus_code

Never by title string. Titles are abbreviated, duplicated across courses, and
change between terms; the natural key is the identity the source itself uses.

Offerings are pre-loaded into a dict in one query rather than looked up per
section. At ~12,000 sections the per-row alternative is ~12,000 round trips.

## Unmatched sections

A section whose offering is absent from the database is **not** inserted and
**not** silently dropped: it is counted and listed in the stats. That happens
legitimately when courses were ingested with `--limit` or `--subject`, so the
section payload covers courses we never loaded. Fabricating the missing course
would invent a Rutgers record, which is exactly what this project forbids.

## Idempotency

Sections upsert on their natural key `(term_code, index_number)`.

Child rows (meetings, instructors, cross-listings) are **replaced** rather than
upserted: they are wholly derived from the parent payload, they have no
source-provided identity beyond their position, and a section's meeting list
can legitimately shrink between terms. Replacing guarantees the stored set
matches the source exactly, with no orphans left behind by a shortened list.
The cost is that unchanged children are rewritten; the benefit is that the
stored state cannot drift from the source.
"""

from __future__ import annotations

import logging
import uuid

from app.models import (
    Course,
    CourseOffering,
    CourseSection,
    SectionCrossListing,
    SectionInstructor,
    SectionMeeting,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from coursepilot_ingestion.section_schemas import NormalizedSection, SectionIngestionStats

logger = logging.getLogger(__name__)

# Refreshed when a section is seen again. Excludes the natural-key columns
# (term_code, index_number): if those changed it would be a different section,
# and rewriting them would corrupt identity.
_MUTABLE_SECTION_FIELDS = (
    "section_number",
    "campus_code",
    "open_status",
    "open_status_text",
    "section_course_type",
    "exam_code",
    "exam_code_text",
    "final_exam",
    "subtitle",
    "section_notes",
    "comments_text",
    "open_to_text",
    "section_eligibility",
    "special_permission_add_code",
    "special_permission_add_description",
    "special_permission_drop_code",
    "special_permission_drop_description",
    "cross_listed_section_type",
)


class SectionLoader:
    """Writes normalized sections into the database."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ #
    # offering resolution
    # ------------------------------------------------------------------ #

    def _offering_lookup(self, term_code: str) -> dict[tuple[str, ...], uuid.UUID]:
        """Map every offering in this term to its id, keyed by the same
        6-tuple a NormalizedSection exposes as `offering_natural_key`."""
        rows = self.session.execute(
            select(
                Course.offering_unit_code,
                Course.subject_code,
                Course.course_number,
                Course.supplement_code,
                CourseOffering.term_code,
                CourseOffering.campus_code,
                CourseOffering.id,
            )
            .join(CourseOffering, CourseOffering.course_id == Course.id)
            .where(CourseOffering.term_code == term_code)
        ).all()
        return {tuple(r[:6]): r[6] for r in rows}

    # ------------------------------------------------------------------ #
    # child rows
    # ------------------------------------------------------------------ #

    def _replace_children(
        self, section: CourseSection, normalized: NormalizedSection, stats: SectionIngestionStats
    ) -> None:
        # Explicit DELETE rather than relying on cascade-on-orphan: this runs
        # as a single statement per child table instead of loading every
        # existing child into the session first.
        for model in (SectionMeeting, SectionInstructor, SectionCrossListing):
            self.session.execute(delete(model).where(model.section_id == section.id))

        for m in normalized.meetings:
            self.session.add(
                SectionMeeting(
                    section_id=section.id,
                    meeting_index=m.meeting_index,
                    meeting_day=m.meeting_day,
                    start_time_military=m.start_time_military,
                    end_time_military=m.end_time_military,
                    meeting_mode_code=m.meeting_mode_code,
                    meeting_mode_desc=m.meeting_mode_desc,
                    building_code=m.building_code,
                    room_number=m.room_number,
                    campus_location=m.campus_location,
                    campus_name=m.campus_name,
                    campus_abbrev=m.campus_abbrev,
                )
            )
        stats.meetings_written += len(normalized.meetings)

        for i in normalized.instructors:
            self.session.add(
                SectionInstructor(
                    section_id=section.id,
                    instructor_index=i.instructor_index,
                    name=i.name,
                )
            )
        stats.instructors_written += len(normalized.instructors)

        for x in normalized.cross_listings:
            self.session.add(
                SectionCrossListing(
                    section_id=section.id,
                    registration_index=x.registration_index,
                    primary_registration_index=x.primary_registration_index,
                    offering_unit_code=x.offering_unit_code,
                    subject_code=x.subject_code,
                    course_number=x.course_number,
                    supplement_code=x.supplement_code,
                    section_number=x.section_number,
                    offering_unit_campus=x.offering_unit_campus,
                )
            )
        stats.cross_listings_written += len(normalized.cross_listings)

    # ------------------------------------------------------------------ #
    # sections
    # ------------------------------------------------------------------ #

    def _upsert_section(
        self,
        normalized: NormalizedSection,
        offering_id: uuid.UUID,
        source_id: uuid.UUID,
        stats: SectionIngestionStats,
    ) -> CourseSection:
        existing = self.session.scalar(
            select(CourseSection).where(
                CourseSection.term_code == normalized.term_code,
                CourseSection.index_number == normalized.index_number,
            )
        )

        if existing is not None:
            changed = False
            for name in _MUTABLE_SECTION_FIELDS:
                new_value = getattr(normalized, name)
                # open_status is a real boolean whose False is meaningful, so
                # unlike the course loader we do not skip falsy values here -
                # only None, which means "the source said nothing".
                if new_value is None:
                    continue
                if getattr(existing, name) != new_value:
                    setattr(existing, name, new_value)
                    changed = True

            # A section moving between offerings is legitimate: Rutgers can
            # re-home a section within a term.
            if existing.offering_id != offering_id:
                existing.offering_id = offering_id
                changed = True

            existing.source_id = source_id
            if changed:
                stats.sections_updated += 1
            return existing

        section = CourseSection(
            offering_id=offering_id,
            term_code=normalized.term_code,
            index_number=normalized.index_number,
            section_number=normalized.section_number,
            campus_code=normalized.campus_code,
            open_status=normalized.open_status,
            open_status_text=normalized.open_status_text,
            section_course_type=normalized.section_course_type,
            exam_code=normalized.exam_code,
            exam_code_text=normalized.exam_code_text,
            final_exam=normalized.final_exam,
            subtitle=normalized.subtitle,
            section_notes=normalized.section_notes,
            comments_text=normalized.comments_text,
            open_to_text=normalized.open_to_text,
            section_eligibility=normalized.section_eligibility,
            special_permission_add_code=normalized.special_permission_add_code,
            special_permission_add_description=normalized.special_permission_add_description,
            special_permission_drop_code=normalized.special_permission_drop_code,
            special_permission_drop_description=normalized.special_permission_drop_description,
            cross_listed_section_type=normalized.cross_listed_section_type,
            source_id=source_id,
        )
        self.session.add(section)
        self.session.flush()
        stats.sections_inserted += 1
        return section

    def load(
        self,
        sections: list[NormalizedSection],
        source_id: uuid.UUID,
        stats: SectionIngestionStats,
        *,
        term_code: str,
    ) -> SectionIngestionStats:
        """Load a batch. The caller owns the transaction boundary."""
        offerings = self._offering_lookup(term_code)
        logger.info("resolved %d offerings for term %s", len(offerings), term_code)

        unmatched_samples: list[str] = []

        for normalized in sections:
            offering_id = offerings.get(normalized.offering_natural_key)
            if offering_id is None:
                stats.unmatched_offering += 1
                if len(unmatched_samples) < 20:
                    unmatched_samples.append(
                        f"section {normalized.index_number}: no offering for "
                        f"{normalized.offering_unit_code}:{normalized.subject_code}:"
                        f"{normalized.course_number}"
                        f"/{normalized.supplement_code or '-'} "
                        f"@{normalized.campus_code} term={normalized.term_code}"
                    )
                continue

            section = self._upsert_section(normalized, offering_id, source_id, stats)
            self._replace_children(section, normalized, stats)

        stats.unmatched_details.extend(unmatched_samples)
        self.session.flush()
        return stats
