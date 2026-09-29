"""Loader: SOC prerequisites -> term-scoped `course_prerequisite` rows (Phase 6.2).

Runs inside the course pipeline, after courses and offerings are loaded, on
the SAME archived payload - so every prerequisite carries the exact SOC
source row (term, archive, content hash) of the courses it belongs to.

## Input is the RAW SOC record, not the normalized course

`SocNormalizer` strips markup from `preReqNotes` before storing
`course.prereq_notes_raw`, and that destroys the operator signal: after
stripping, `<em> OR </em>` is a bare upper-case OR, indistinguishable from
the OR in a title like "LIFE AND SOCIAL SCIENCES". The parser therefore reads
the raw string, and the raw string is what is stored.

## Reload semantics

One row per offering, keyed on `offering_id`:

  * same term, same payload       -> nothing changes (not even updated_at)
  * same term, changed payload    -> the row is updated in place: an offering
                                     has one prerequisite, the latest published
                                     for that term, and the source row records
                                     which payload it came from
  * same term, prerequisite gone  -> the row is removed
  * different term                -> a different offering, a different row;
                                     earlier terms are never touched

References are synced to the expression: added, kept, or removed, and each
is (re-)resolved to a `course` row when one exists.
"""

from __future__ import annotations

import hashlib
import logging

from app.domain.prerequisites import (
    PARSER_VERSION,
    COURSE_KEY,
    condition_kinds,
    parse,
    to_json,
)
from app.models import Course, CourseOffering, CoursePrerequisite, DataSource, PrerequisiteReference
from sqlalchemy import select
from sqlalchemy.orm import Session

from coursepilot_ingestion.schemas import IngestionStats, NormalizedCourse, RawSocCourse

logger = logging.getLogger(__name__)


def _raw_key(raw: RawSocCourse) -> tuple[str, str, str, str, str]:
    return (
        raw.offeringUnitCode.strip(), raw.subject.strip(), raw.courseNumber.strip(),
        (raw.supplementCode or "").strip(), (raw.campusCode or "").strip() or "UNKNOWN",
    )


class PrerequisiteLoader:
    def __init__(self, session: Session) -> None:
        self.session = session

    def load(
        self,
        raws: list[RawSocCourse],
        courses: list[NormalizedCourse],
        source: DataSource,
        stats: IngestionStats,
    ) -> IngestionStats:
        counts = stats.prerequisites
        raw_by_key = {_raw_key(r): r for r in raws}
        term_codes = {c.term_code for c in courses}

        # Preload - one query each - so the loader is not N+1 over ~4,400 courses.
        offerings = {
            (c.offering_unit_code, c.subject_code, c.course_number, c.supplement_code,
             o.campus_code): o
            for o, c in self.session.execute(
                select(CourseOffering, Course)
                .join(Course, Course.id == CourseOffering.course_id)
                .where(CourseOffering.term_code.in_(term_codes))
            ).all()
        }
        existing = {
            p.offering_id: p
            for p in self.session.scalars(
                select(CoursePrerequisite).where(CoursePrerequisite.term_code.in_(term_codes))
            ).all()
        }
        course_ids = dict(self.session.execute(
            select(Course.course_string, Course.id).where(Course.supplement_code == "")
        ).all())

        for normalized in courses:
            key = (*normalized.natural_key, normalized.campus_code)
            raw = raw_by_key.get(key)
            offering = offerings.get(key)
            if raw is None or offering is None:
                counts["skipped_no_offering"] = counts.get("skipped_no_offering", 0) + 1
                continue

            raw_text = raw.preReqNotes if (raw.preReqNotes or "").strip() else None
            note = getattr(raw, "courseNotes", None)
            kinds = condition_kinds(note)
            note = note if kinds else None
            current = existing.get(offering.id)

            if raw_text is None and note is None:
                if current is not None:
                    self.session.delete(current)
                    counts["deleted"] = counts.get("deleted", 0) + 1
                continue

            result = parse(raw_text) if raw_text else None
            fields = {
                "course_id": offering.course_id,
                "term_code": offering.term_code,
                "raw_text": raw_text,
                "raw_text_sha256": (hashlib.sha256(raw_text.encode()).hexdigest()
                                    if raw_text else None),
                "condition_note": note,
                "condition_kinds": ",".join(kinds) or None,
                "classification": (result.classification.value if result
                                   else "condition_note_only"),
                "expression": (to_json(result.expression)
                               if result and result.expression is not None else None),
                "canonical_text": result.canonical_text if result else None,
                "parse_detail": result.detail if result else None,
                "parser_version": PARSER_VERSION,
                "source_id": source.id,
            }
            references = sorted(set(result.references if result else ())
                                | set(COURSE_KEY.findall(note or "")))

            if current is None:
                current = CoursePrerequisite(offering_id=offering.id, **fields)
                self.session.add(current)
                counts["inserted"] = counts.get("inserted", 0) + 1
            elif any(getattr(current, k) != v for k, v in fields.items() if k != "source_id"):
                for k, v in fields.items():
                    setattr(current, k, v)
                counts["updated"] = counts.get("updated", 0) + 1
            else:
                counts["unchanged"] = counts.get("unchanged", 0) + 1

            self._sync_references(current, references, course_ids, counts)
            counts[f"class:{fields['classification']}"] = (
                counts.get(f"class:{fields['classification']}", 0) + 1)
            if kinds:
                counts["with_condition_note"] = counts.get("with_condition_note", 0) + 1

        self.session.flush()
        return stats

    @staticmethod
    def _sync_references(prereq, references, course_ids, counts) -> None:
        wanted = {ref: course_ids.get(ref) for ref in references}
        for ref in list(prereq.references):
            if ref.course_string not in wanted:
                prereq.references.remove(ref)
            elif ref.course_id != wanted[ref.course_string]:
                ref.course_id = wanted[ref.course_string]
        have = {r.course_string for r in prereq.references}
        for course_string, course_id in wanted.items():
            if course_string not in have:
                prereq.references.append(
                    PrerequisiteReference(course_string=course_string, course_id=course_id))
        for course_string, course_id in wanted.items():
            counts["references"] = counts.get("references", 0) + 1
            if course_id is None:
                counts["references_unresolved"] = counts.get("references_unresolved", 0) + 1
