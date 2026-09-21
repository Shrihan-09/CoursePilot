"""The search contract (Phase 5.0).

One interface, several implementations. A caller asks for courses matching a
query and must not need to know whether the answer came from BM25, from
embeddings, or from a fusion of both - that is what makes the strategies
comparable rather than entangled.

## What a search result is NOT

A `SearchResult` is evidence, never a decision. It says "this course's text
matched your words well". It does not say the course counts toward anything,
satisfies anything, or is available to a given student. Those questions
belong to `app.services.audit`, and this package must never be consulted for
them.

The scores from different retrievers are **not comparable to one another**.
A BM25 score of 12.4 and a cosine similarity of 0.83 measure different
things on different scales, which is exactly why fusing them needs more care
than addition (see `hybrid.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.services.search.documents import CourseDocument, Provenance


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One retrieved course, with why it was retrieved.

    `matched_fields` is what makes a result explainable: a hit on `code` is a
    lookup, a hit on `description` is a topical match, and a future answer
    should be able to tell a student which one happened.
    """

    document_id: str
    course_key: str
    title: str
    score: float
    matched_fields: tuple[str, ...] = ()
    retriever: str = ""
    #: Carried through so a citation can be produced without a second query.
    course_provenance: Provenance | None = None
    description_provenance: Provenance | None = None
    #: The rank this result held in each retriever that contributed to it.
    #: Populated by fusion; empty for a single retriever.
    component_ranks: dict[str, int] = field(default_factory=dict)

    def citation(self) -> str:
        """A short, source-grounded attribution - never invented.

        Falls back to naming only the source when no year was recorded,
        rather than guessing one.
        """
        provenance = self.description_provenance or self.course_provenance
        if provenance is None:
            return "unattributed"
        if provenance.catalog_year:
            return f"{provenance.source_kind} {provenance.catalog_year}"
        return provenance.source_kind


@runtime_checkable
class CourseSearcher(Protocol):
    """Anything that can rank course documents for a query."""

    name: str

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]: ...


def result_from_document(
    document: CourseDocument,
    score: float,
    matched_fields: tuple[str, ...],
    retriever: str,
) -> SearchResult:
    return SearchResult(
        document_id=document.document_id,
        course_key=document.course_key,
        title=document.title,
        score=score,
        matched_fields=matched_fields,
        retriever=retriever,
        course_provenance=document.course_provenance,
        description_provenance=document.description_provenance,
    )


__all__ = ["CourseSearcher", "SearchResult", "result_from_document"]
