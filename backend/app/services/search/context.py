"""RAG context assembly (Phase 5.0, Parts J, K and L).

Turns a query into a **bounded, deduplicated, source-grounded** context that a
future explanation layer can read. It assembles evidence. It does not answer
academic questions, and it contains no model call.

## The boundary, stated as code

```
RAG can retrieve            the Degree Engine must decide
-----------------           ----------------------------
course titles               requirement satisfaction
course descriptions         degree progress
catalog information         category coverage
subject names               course allocation
source documentation        baseline regressions
                            sharing policy
                            credit accounting
```

If a generated answer ever conflicts with the Degree Engine, **the Degree
Engine wins**. That is not a preference; the engine is deterministic,
exhaustively tested and verified against an independent oracle, and the
retrieval layer is a text-similarity heuristic over course titles.

`AcademicQuestion` exists so this boundary is machine-checkable rather than
merely documented: a query that looks like it is asking whether something
counts is flagged, and the flag tells the caller to consult the engine
instead of treating retrieved text as an answer.

## Why the context is bounded

An LLM prompt is not a database. Dumping 4,415 courses into a prompt buries
the relevant few, costs tokens, and invites the model to synthesise an answer
from noise. The context carries a small number of documents, each trimmed to
a character budget, with every snippet attributable to a source.

## Why snippets are never rewritten

Text in the context is copied verbatim from the authoritative source and
carries its provenance. Summarising or paraphrasing at assembly time would
create generated text that later looks like catalog text - exactly the
substitution Part C forbids.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.search.contract import CourseSearcher, SearchResult
from app.services.search.documents import CourseDocument

#: Per-snippet character budget. A catalog description is typically a few
#: hundred characters; this keeps one course from crowding out the rest.
DEFAULT_SNIPPET_CHARS = 400

#: How many documents a context may carry. Small on purpose - see the
#: module docstring on why a prompt is not a database.
DEFAULT_MAX_DOCUMENTS = 8


#: Phrases that indicate the question needs the DETERMINISTIC engine, not
#: retrieved text. Deliberately conservative: a false positive only adds a
#: flag, while a false negative risks retrieval answering an academic
#: question.
_ACADEMIC_PATTERNS = (
    r"\bsatisf(y|ies|ied)\b",
    r"\bfulfill?s?\b",
    r"\bcount(s|ed)?\s+(toward|towards|for)\b",
    r"\bam i\b",
    r"\bdo i (need|have)\b",
    r"\bhow many (credits|courses)\b",
    r"\bgraduat(e|ion)\b",
    r"\bon track\b",
    r"\bremaining\b",
    r"\beligible\b",
)


def looks_like_an_academic_question(query: str) -> bool:
    """Whether the query is asking something only the Degree Engine can answer.

    Retrieval may still run - the source text is useful supporting evidence -
    but the caller must not present retrieved text as the verdict.
    """
    lowered = query.lower()
    return any(re.search(pattern, lowered) for pattern in _ACADEMIC_PATTERNS)


@dataclass(frozen=True, slots=True)
class ContextSnippet:
    """One piece of retrieved, attributable text."""

    course_key: str
    title: str
    text: str
    field_name: str
    source_kind: str
    catalog_year: str | None
    retrieval_score: float
    matched_fields: tuple[str, ...] = ()

    def citation(self) -> str:
        if self.catalog_year:
            return f"{self.source_kind} {self.catalog_year}"
        return self.source_kind


@dataclass(frozen=True, slots=True)
class RagContext:
    """Everything an explanation layer is allowed to rely on.

    `requires_degree_engine` is the load-bearing field: when True, the
    caller must obtain the academic answer from `app.services.audit` and use
    these snippets only as supporting description.
    """

    query: str
    snippets: tuple[ContextSnippet, ...]
    requires_degree_engine: bool
    expansions_applied: tuple[str, ...] = ()
    truncated: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def course_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(s.course_key for s in self.snippets))

    def total_chars(self) -> int:
        return sum(len(s.text) for s in self.snippets)

    def render(self) -> str:
        """A plain-text rendering with citations attached to each snippet.

        Every line names its source, so an answer built from this cannot
        silently present catalog text and generated text as the same thing.
        """
        lines: list[str] = []
        if self.requires_degree_engine:
            lines.append(
                "[This question requires the deterministic Degree Engine. "
                "The following is supporting course information only.]"
            )
        for snippet in self.snippets:
            lines.append(
                f"{snippet.course_key} {snippet.title} "
                f"[{snippet.field_name}, {snippet.citation()}]\n{snippet.text}"
            )
        return "\n\n".join(lines)


def _trim(text: str, budget: int) -> tuple[str, bool]:
    """Trim on a word boundary; never mid-word, never with invented text."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= budget:
        return collapsed, False
    cut = collapsed[:budget].rsplit(" ", 1)[0]
    return cut, True


def assemble_context(
    searcher: CourseSearcher,
    query: str,
    documents_by_key: dict[str, CourseDocument],
    *,
    limit: int = DEFAULT_MAX_DOCUMENTS,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> RagContext:
    """Retrieve, deduplicate, bound, and attribute.

    `documents_by_key` supplies the full text; a `SearchResult` deliberately
    carries only what ranking needed, so the context layer fetches the body
    rather than the retriever hauling it around.
    """
    results = searcher.search(query, limit=limit)

    snippets: list[ContextSnippet] = []
    seen: set[str] = set()
    truncated = False

    for result in results:
        # Deduplicate by course: the same course must not occupy two slots
        # of a small budget.
        if result.course_key in seen:
            continue
        seen.add(result.course_key)

        document = documents_by_key.get(result.course_key)
        if document is None:
            continue

        # Prefer the description - it is the text a student actually wants -
        # and fall back to the title, which every course has.
        if document.has_description:
            body, cut = _trim(document.description or "", snippet_chars)
            provenance = document.description_provenance
            field_name = "description"
        else:
            body, cut = _trim(document.title, snippet_chars)
            provenance = document.course_provenance
            field_name = "title"
        truncated = truncated or cut

        snippets.append(
            ContextSnippet(
                course_key=document.course_key,
                title=document.title,
                text=body,
                field_name=field_name,
                source_kind=provenance.source_kind if provenance else "unattributed",
                catalog_year=provenance.catalog_year if provenance else None,
                retrieval_score=result.score,
                matched_fields=result.matched_fields,
            )
        )

    notes: list[str] = []
    needs_engine = looks_like_an_academic_question(query)
    if needs_engine:
        notes.append(
            "Query appears to ask an academic question. Requirement "
            "satisfaction must come from app.services.audit, never from "
            "retrieved text."
        )
    if not snippets:
        notes.append("No course matched this query; no context was assembled.")

    expansions = tuple(
        e.term for e in getattr(searcher, "last_expansions", ()) or ()
    )

    return RagContext(
        query=query,
        snippets=tuple(snippets),
        requires_degree_engine=needs_engine,
        expansions_applied=expansions,
        truncated=truncated,
        notes=tuple(notes),
    )


__all__ = [
    "DEFAULT_MAX_DOCUMENTS",
    "DEFAULT_SNIPPET_CHARS",
    "ContextSnippet",
    "RagContext",
    "assemble_context",
    "looks_like_an_academic_question",
]
