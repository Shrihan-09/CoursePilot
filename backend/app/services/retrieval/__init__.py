"""Retrieval — finds candidate authoritative records.

NOT IMPLEMENTED YET. Contract only.

The `Retriever` protocol is deliberately uniform across BM25, vector, hybrid,
and reranked implementations. That is the whole point: it lets us swap
strategies behind one interface and measure them against a shared eval set
rather than assuming semantic search is better. See docs/RAG_ARCHITECTURE.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    text: str
    score: float
    # Which record this chunk was derived from. Retrieval returns pointers
    # into the normalized database; it is not itself the source of truth.
    entity_type: str | None = None
    entity_id: str | None = None
    source_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Retriever(Protocol):
    name: str

    async def retrieve(
        self,
        query: str,
        *,
        limit: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        """Return candidates ranked best-first.

        `filters` carries hard constraints (term, program, subject). These are
        applied as database predicates, not as soft ranking signals — a course
        from the wrong term is wrong regardless of how well it matches.
        """
        ...
