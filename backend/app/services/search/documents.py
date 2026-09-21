"""Retrieval documents derived from authoritative course data (Phase 5.0).

## What this is, and what it is not

A `CourseDocument` is a **derived, read-only view** of data that already
lives in `course`, `subject` and `catalog_course_entry`. It is not a second
copy of the catalog and it is not authoritative for anything. Rebuilding it
from the database must always be possible, and rebuilding it must always be
the way to fix it.

That matters because of the architectural rule this phase exists to protect:

    RAG retrieves and explains. The deterministic Degree Engine decides.

Nothing in this package may be consulted to answer whether a requirement is
satisfied, whether a course counts, or whether an allocation is valid. Those
answers come from `app.services.audit`.

## Source authority is preserved, not flattened

The authority matrix established in Phase 4 still holds, so a document
records WHERE each piece of text came from rather than merging everything
into one blob:

| Field | Authoritative source |
|---|---|
| course identity, title, credits, level | Rutgers SOC |
| subject name | Rutgers SOC (`subject.description`) |
| catalog description | Rutgers Catalog (`catalog_course_entry`) |

A description is therefore attributed to the catalog and carries the catalog
year it was published under, which is usually NOT the SOC term the course row
came from. Collapsing those two into one "year" would be the kind of quiet
provenance loss this project has refused since Phase 1.

## Measured corpus shape (2026-27 dev database)

```
courses                       4,415
with a title                  4,415   average 27 characters, UPPERCASE
with a catalog description       31   CS only (88 catalog rows dedupe to
                                      31 distinct linked courses)
distinct subjects               243
```

**98% of courses have a title and nothing else.** That is a property of the
sources - SOC publishes no descriptions - not of this code, and it bounds
what any retrieval method can do on conceptual queries. Phase 5.0 measures
against this corpus honestly rather than reporting scores from a richer one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CatalogCourseEntry, Course, Subject

#: What a document describes. An open set - `requirement` and `program` are
#: the obvious future additions and would carry their own provenance.
DOCUMENT_TYPE_COURSE = "course"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a piece of retrieved text came from.

    Carried on every document so a future answer can say "according to the
    Rutgers 2026-27 catalog" instead of presenting generated text as fact.
    A URL is included only when the ingestion pipeline actually stored one;
    it is never constructed.
    """

    source_kind: str
    catalog_year: str | None = None
    term_code: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class CourseDocument:
    """One retrievable course.

    `document_id` is the natural key, not a surrogate: Phase 1 measured that
    `course_string` alone is NOT unique (4,389 distinct over 4,400 rows), so
    identity is `(course_string, supplement_code)`. Using a UUID here would
    make the index unreproducible across a re-ingest, which is the trap
    recorded in DATA_MODEL.md 16.3.
    """

    document_id: str
    course_key: str
    subject_code: str
    subject_name: str | None
    course_number: str
    title: str
    description: str | None
    credits: Decimal | None
    level: str | None
    document_type: str = DOCUMENT_TYPE_COURSE
    #: Per-text provenance: the course row and the description can come from
    #: different sources and different years.
    course_provenance: Provenance | None = None
    description_provenance: Provenance | None = None

    @property
    def has_description(self) -> bool:
        return bool(self.description and self.description.strip())

    def field_text(self) -> dict[str, str]:
        """The searchable fields, keyed by name.

        Kept as separate fields rather than one concatenated blob so a
        retriever can weight them and report WHICH field matched - a result
        that matched a course code is a different kind of answer from one
        that matched a description.
        """
        return {
            "code": self.course_key,
            "subject": self.subject_name or "",
            "number": self.course_number,
            "title": self.title or "",
            "description": self.description or "",
        }


def build_course_documents(session: Session) -> list[CourseDocument]:
    """Derive documents from the database. Deterministic and rebuildable.

    Ordered by the natural key so two builds over the same data produce the
    same list in the same order - the index, and therefore every tie in
    ranking, is reproducible.
    """
    subjects = {
        (s.offering_unit_code, s.code): s.description
        for s in session.scalars(select(Subject)).all()
    }

    # Catalog descriptions, keyed by the course they were linked to. An entry
    # that never resolved to a course row is deliberately skipped rather than
    # attached by string-matching - Phase 3.5 kept unlinked entries precisely
    # so they would not be silently guessed at.
    descriptions: dict[str, tuple[str, str | None]] = {}
    for entry in session.scalars(select(CatalogCourseEntry)).all():
        if entry.course_id is None or not entry.description:
            continue
        key = str(entry.course_id)
        if key not in descriptions:
            descriptions[key] = (entry.description, entry.catalog_year)

    documents: list[CourseDocument] = []
    rows = session.scalars(
        select(Course).order_by(Course.course_string, Course.supplement_code)
    ).all()
    for course in rows:
        description, catalog_year = descriptions.get(str(course.id), (None, None))
        documents.append(
            CourseDocument(
                document_id=f"{course.course_string}|{course.supplement_code}",
                course_key=course.course_string,
                subject_code=course.subject_code,
                subject_name=subjects.get(
                    (course.offering_unit_code, course.subject_code)
                ),
                course_number=course.course_number,
                title=course.title or "",
                description=description,
                credits=course.credits,
                level=course.level,
                course_provenance=Provenance(source_kind="rutgers_soc"),
                description_provenance=(
                    Provenance(
                        source_kind="rutgers_catalog", catalog_year=catalog_year
                    )
                    if description
                    else None
                ),
            )
        )
    return documents


@dataclass(frozen=True, slots=True)
class CorpusStats:
    """What the corpus actually contains - reported, never assumed."""

    documents: int
    with_description: int
    subjects: int
    mean_title_chars: float = 0.0

    def summary(self) -> str:
        pct = (100 * self.with_description / self.documents) if self.documents else 0
        return (
            f"{self.documents} documents, {self.with_description} with a "
            f"description ({pct:.1f}%), {self.subjects} subjects, "
            f"mean title {self.mean_title_chars:.0f} chars"
        )


def corpus_stats(documents: list[CourseDocument]) -> CorpusStats:
    if not documents:
        return CorpusStats(0, 0, 0)
    return CorpusStats(
        documents=len(documents),
        with_description=sum(1 for d in documents if d.has_description),
        subjects=len({d.subject_code for d in documents}),
        mean_title_chars=sum(len(d.title) for d in documents) / len(documents),
    )


__all__ = [
    "DOCUMENT_TYPE_COURSE",
    "CorpusStats",
    "CourseDocument",
    "Provenance",
    "build_course_documents",
    "corpus_stats",
]
