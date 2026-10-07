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

## Phase 6.4

  * A `sectionNotes` text that states a prerequisite condition is a
    COURSE-level fact only when every section of the offering publishes the
    same text; it is stored as `section_condition_note`. Section-specific
    notes stay section-level and are not loaded.
  * `interpreted_conditions` is app.domain.conditions' reading of the
    condition texts (minimum grade, placement/permission alternatives, what is
    still uninterpreted), versioned by `condition_parser_version`.
  * Co-requisites are loaded into `course_corequisite` (one row per
    offering) from `courseNotes`, or from section notes when EVERY section
    yields the same parsed rule. Re-running is idempotent; a co-requisite that
    disappears is removed.
  * Phase 6.6.1: a co-requisite on SOME sections only is stored too, as
    `sectionNotes:some` with "published on N of M sections" in parse_detail
    (before, nothing was stored and eligibility said SATISFIED).
"""

from __future__ import annotations

import hashlib
import logging

from app.domain.conditions import CONDITION_PARSER_VERSION, clean, interpret
from app.domain.corequisites import COREQUISITE_PARSER_VERSION, CoreqParse, parse_note
from app.domain.prerequisites import (
    PARSER_VERSION,
    COURSE_KEY,
    condition_kinds,
    course_keys,
    parse,
    to_json,
)
from app.models import (
    Course,
    CourseCorequisite,
    CourseOffering,
    CoursePrerequisite,
    DataSource,
    PrerequisiteReference,
)
from sqlalchemy import select, update
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
        by_subject_number: dict[tuple[str, str], list[str]] = {}
        for course_string in course_ids:
            unit, subject, number = course_string.split(":")
            by_subject_number.setdefault((subject, number), []).append(course_string)
        existing_coreqs = {
            c.offering_id: c
            for c in self.session.scalars(
                select(CourseCorequisite).where(CourseCorequisite.term_code.in_(term_codes))
            ).all()
        }

        for normalized in courses:
            key = (*normalized.natural_key, normalized.campus_code)
            raw = raw_by_key.get(key)
            offering = offerings.get(key)
            if raw is None or offering is None:
                counts["skipped_no_offering"] = counts.get("skipped_no_offering", 0) + 1
                continue

            raw_text = raw.preReqNotes if (raw.preReqNotes or "").strip() else None
            course_note_raw = getattr(raw, "courseNotes", None)
            note = course_note_raw
            kinds = condition_kinds(note)
            note = note if kinds else None
            section_texts = [s.get("sectionNotes") for s in (raw.sections or [])]
            section_note = _uniform(section_texts)
            section_note = section_note if condition_kinds(section_note) else None
            kinds = tuple(dict.fromkeys(kinds + condition_kinds(section_note)))
            current = existing.get(offering.id)
            resolve = _resolver(by_subject_number, normalized.offering_unit_code)

            self._load_corequisite(offering, course_note_raw, section_texts, source, resolve,
                                   existing_coreqs.get(offering.id), counts)

            if raw_text is None and note is None and section_note is None:
                if current is not None:
                    self.session.delete(current)
                    counts["deleted"] = counts.get("deleted", 0) + 1
                continue

            result = parse(raw_text) if raw_text else None
            expression_courses = (set(course_keys(result.expression))
                                  if result and result.expression is not None else set())
            interpreted = interpret([note, section_note], expression_courses, resolve)
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
                "section_condition_note": section_note,
                "interpreted_conditions": interpreted,
                "condition_parser_version": CONDITION_PARSER_VERSION if interpreted else None,
                "source_id": source.id,
            }
            references = sorted(set(result.references if result else ())
                                | set(COURSE_KEY.findall(note or ""))
                                | set(COURSE_KEY.findall(section_note or "")))

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
            if interpreted:
                if interpreted.get("minimum_grade"):
                    counts["condition:minimum_grade"] = counts.get("condition:minimum_grade", 0) + 1
                if interpreted.get("alternatives"):
                    counts["condition:alternatives"] = counts.get("condition:alternatives", 0) + 1
                if interpreted.get("uninterpreted"):
                    counts["condition:uninterpreted"] = (
                        counts.get("condition:uninterpreted", 0) + 1)

        self.session.flush()
        counts["references_resolved_late"] = self._resolve_outstanding()
        return stats

    def _load_corequisite(self, offering, course_note, section_texts, source, resolve,
                          current, counts) -> None:
        """Upsert / remove the offering's co-requisite (Phase 6.4)."""
        parsed = [(p, "courseNotes", course_note) for p in parse_note(course_note, resolve)]
        coverage = None
        if not parsed and section_texts:
            per_section = [parse_note(t, resolve) for t in section_texts]
            canon = {tuple((p.classification, p.canonical_text or "") for p in parses)
                     for parses in per_section}
            if len(canon) == 1 and per_section[0]:
                texts = sorted({clean(t) for t in section_texts if t})
                parsed = [(p, "sectionNotes:all", " || ".join(texts)) for p in per_section[0]]
            else:
                # Phase 6.6.1: a co-requisite published on SOME sections only
                # (60 offerings across the archive, e.g. 01:750:229 on 26 of
                # 28 sections) used to store nothing - eligibility then said
                # SATISFIED. It is stored as "sectionNotes:some": met when the
                # co-requisite is, UNKNOWN otherwise (never SATISFIED).
                marked = [(t, ps) for t, ps in zip(section_texts, per_section, strict=True)
                          if ps]
                if marked:
                    coverage = f"published on {len(marked)} of {len(section_texts)} sections"
                    sigs = {tuple((p.classification, p.canonical_text or "") for p in ps)
                            for _, ps in marked}
                    texts = " || ".join(sorted({clean(t) for t, _ in marked}))
                    if len(sigs) == 1:
                        parsed = [(p, "sectionNotes:some", texts) for p in marked[0][1]]
                    else:
                        first = marked[0][1][0]
                        parsed = [(CoreqParse(first.kind, "unsupported", None, (),
                                              "sections publish different co-requisites"),
                                   "sectionNotes:some", texts)]
        if not parsed:
            if current is not None:
                self.session.delete(current)
                counts["corequisites_deleted"] = counts.get("corequisites_deleted", 0) + 1
            return
        if len(parsed) > 1:
            # Several co-requisite clauses: one row, combined only if all parsed.
            from app.domain.prerequisites import AllOf, canonicalize, to_text
            if all(p.classification == "parsed" for p, _, _ in parsed):
                expr = canonicalize(AllOf(tuple(p.expression for p, _, _ in parsed)))
                classification, canonical, detail = "parsed", to_text(expr), None
            else:
                expr, classification, canonical = None, "unsupported", None
                detail = "; ".join(p.detail or "" for p, _, _ in parsed if p.detail)
        else:
            p = parsed[0][0]
            expr, classification = p.expression, p.classification
            canonical, detail = p.canonical_text, p.detail
        fields = {
            "course_id": offering.course_id, "term_code": offering.term_code,
            "raw_text": parsed[0][2], "source_field": parsed[0][1],
            "classification": classification,
            "expression": to_json(expr) if expr is not None else None,
            "canonical_text": canonical,
            "parse_detail": "; ".join(x for x in (coverage, detail) if x) or None,
            "parser_version": COREQUISITE_PARSER_VERSION, "source_id": source.id,
        }
        if current is None:
            self.session.add(CourseCorequisite(offering_id=offering.id, **fields))
            counts["corequisites_inserted"] = counts.get("corequisites_inserted", 0) + 1
        elif any(getattr(current, k) != v for k, v in fields.items() if k != "source_id"):
            for k, v in fields.items():
                setattr(current, k, v)
            counts["corequisites_updated"] = counts.get("corequisites_updated", 0) + 1
        else:
            counts["corequisites_unchanged"] = counts.get("corequisites_unchanged", 0) + 1
        counts[f"corequisite:{classification}"] = counts.get(f"corequisite:{classification}", 0) + 1

    def _resolve_outstanding(self) -> int:
        """Resolve EVERY still-unresolved reference whose course now exists.

        Found loading the archives in Phase 6.2: resolution at load time made
        the result depend on load ORDER. Fall 2025 loaded first and left
        1,184 references unresolved - including courses Spring 2026 then
        loaded minutes later. One set-based UPDATE after each load makes the
        final state independent of order; it only ever fills a NULL, never
        rewrites a resolved reference.
        """
        match = (
            select(Course.id)
            .where(Course.course_string == PrerequisiteReference.course_string,
                   Course.supplement_code == "")
            .limit(1)
            .scalar_subquery()
        )
        resolvable = (
            select(Course.id)
            .where(Course.course_string == PrerequisiteReference.course_string,
                   Course.supplement_code == "")
            .exists()
        )
        result = self.session.execute(
            update(PrerequisiteReference)
            .where(PrerequisiteReference.course_id.is_(None), resolvable)
            .values(course_id=match)
            .execution_options(synchronize_session=False)
        )
        self.session.expire_all()
        return result.rowcount or 0

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


def _uniform(texts: list) -> str | None:
    """The section note EVERY section publishes, or None."""
    if not texts:
        return None
    cleaned = {(t or "").strip() for t in texts}
    if len(cleaned) != 1:
        return None
    only = cleaned.pop()
    return only or None


def _resolver(by_subject_number: dict, own_unit: str):
    """Resolve a short code "subject:number" against the course table.

    The course's own offering unit first (Rutgers shorthand within a school),
    else the single course with that subject and number; ambiguous or absent
    resolves to nothing - never a guessed unit.
    """
    def resolve(subject: str, number: str) -> str | None:
        options = by_subject_number.get((subject, number), [])
        own = f"{own_unit}:{subject}:{number}"
        if own in options:
            return own
        return options[0] if len(options) == 1 else None
    return resolve
