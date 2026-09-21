"""Course retrieval for CoursePilot (Phase 5.0).

RAG retrieves and explains. The deterministic Degree Engine decides academic
correctness. Nothing in this package may be consulted to answer whether a
requirement is satisfied - see `app.services.audit` for that.
"""

from app.services.search.bm25 import BM25Searcher, build_bm25
from app.services.search.contract import CourseSearcher, SearchResult
from app.services.search.documents import (
    CourseDocument,
    Provenance,
    build_course_documents,
    corpus_stats,
)
from app.services.search.evaluation import EvalQuery, evaluate

__all__ = [
    "BM25Searcher",
    "CourseDocument",
    "CourseSearcher",
    "EvalQuery",
    "Provenance",
    "SearchResult",
    "build_bm25",
    "build_course_documents",
    "corpus_stats",
    "evaluate",
]
