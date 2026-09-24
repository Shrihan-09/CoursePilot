"""The BM25 index lifecycle (Phase 5.11).

> The BM25 index is **derived data**. It is never authoritative, and
> deleting all of it costs latency and nothing else.

## The problem, measured

Before this, `build_course_documents` + `build_bm25` ran inside every
explanation request:

```
build_course_documents   130.36 ms
build_bm25               133.21 ms
                         --------
per request              ~264 ms   of a ~336 ms endpoint  (78%)
one search, built index    0.09 ms
```

## The invariant

```
same search inputs   -> same index, reused
changed search inputs -> the old index cannot be served
```

## Why not the obvious designs

| design | rejected because |
|---|---|
| build at startup | the app is documented to boot with Postgres down (`/ready` reports it); building at startup breaks that invariant |
| lazy singleton, no invalidation | silently serves a stale catalog after ingestion - the one outcome the brief forbids |
| explicit `invalidate()` calls from ingestion | ingestion is a **separate process**; it cannot reach this one's memory. Invalidation by discipline, and here the discipline is not even possible |
| **version-keyed reuse** | **chosen** |

The version is maintained by PostgreSQL triggers on the three tables the
corpus actually reads (see `app/models/search_version.py`), so invalidation
is a property of the data rather than of anyone remembering a call. Reading
it costs 0.73 ms against a 264 ms rebuild.

## Identity has a code half too

`CURATED_EXPANSIONS` lives in `synonyms.py`, not in a table, so a database
counter cannot see a change to it. The published index therefore carries:

```
v:<search_version>|c:<corpus-code-version>
```

The second half must be bumped when document construction, tokenization,
field weights or the curated expansions change - the same discipline
`AUDIT_ENGINE_VERSION` carries, and honestly labelled as discipline.

## Publication is atomic, and the index is immutable

A rebuild constructs a completely new `PublishedIndex` and swaps the
reference. The live index is never cleared first and never mutated in
place, so a concurrent reader either sees the whole old index or the whole
new one - never a half-built one. `BM25Searcher` mutates nothing after
construction (verified: every assignment lives in `BM25Index.build`).

**`ExpandingSearcher` is NOT shared.** It stores `last_expansions` on every
`search()` call, and `context.py` reads that field afterwards - so sharing
one across requests would let concurrent callers read each other's
expansions. It is a two-field wrapper costing ~0 ms, so each request gets
its own around the shared immutable index. This is the specific reason
"just make it a singleton" would have been wrong.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.metrics import (
    SEARCH_INDEX_BUILD_DURATION,
    SEARCH_INDEX_BUILD_FAILURES,
    SEARCH_INDEX_BUILDS,
    SEARCH_INDEX_CONCURRENT_SUPPRESSED,
    SEARCH_INDEX_REUSE,
    SEARCH_INDEX_STALE_SERVED,
    SEARCH_INDEX_VERSION_UNAVAILABLE,
    get_metrics,
)
from app.services.search.bm25 import BM25Searcher, build_bm25
from app.services.search.documents import CourseDocument, build_course_documents

logger = logging.getLogger(__name__)

#: Bump when the CORPUS or the RANKING can change for unchanged database
#: rows: document construction, tokenization, field weights, or the curated
#: expansions in `synonyms.py`.
#:
#: This half is discipline, not construction - a database trigger cannot see
#: a code change. Stated plainly rather than pretended away, exactly as
#: `AUDIT_ENGINE_VERSION` is.
CORPUS_CODE_VERSION = "5.11.0"


class IndexUnavailable(RuntimeError):
    """No usable index exists and one could not be built.

    Callers degrade rather than fail: retrieval is descriptive context, and
    the Degree Engine facts an explanation rests on do not come from search.
    """


@dataclass(frozen=True, slots=True)
class PublishedIndex:
    """An immutable, atomically published index.

    Frozen because publication is a reference swap: a reader holding this
    object must never see it change underneath them.
    """

    searcher: BM25Searcher
    documents_by_key: dict[str, CourseDocument]
    version: str
    document_count: int
    build_ms: float
    built_at: float = field(default_factory=time.time)


def _code_version() -> str:
    from app.services.search.synonyms import CURATED_EXPANSIONS

    # The expansion count is a cheap tripwire: adding or removing a curated
    # expansion changes the identity even if nobody bumped the constant.
    # It cannot detect an edit to an existing expansion - that is what
    # CORPUS_CODE_VERSION is for.
    return f"{CORPUS_CODE_VERSION}/{len(CURATED_EXPANSIONS)}"


def read_search_version(session: Session) -> int | None:
    """The trigger-maintained counter, or None when unavailable.

    Never raises. An absent table (SQLite, an unmigrated database) means
    "cannot prove freshness", which the registry turns into "rebuild every
    time" - the pre-5.11 behaviour: slower, and equally correct.
    """
    try:
        return session.execute(
            text("SELECT version FROM search_version WHERE id = 1")
        ).scalar()
    except Exception:
        logger.debug("search_version_unavailable", exc_info=True)
        # A failed statement aborts a PostgreSQL transaction, and the caller
        # is about to query the same session to build the corpus.
        try:
            session.rollback()
        except Exception:
            logger.debug("search_version_rollback_failed", exc_info=True)
        return None


def corpus_token(session: Session) -> str | None:
    """The full index identity, or None when freshness cannot be proven."""
    version = read_search_version(session)
    if version is None:
        return None
    return f"v:{version}|c:{_code_version()}"


class SearchIndexRegistry:
    """Holds at most one published index per process.

    Per process, and stated as such - see the multi-worker note in
    DATA_MODEL. Each worker builds and validates its own copy; there is no
    shared memory and no cross-process invalidation, because each worker
    independently reads the same database counter and reaches the same
    conclusion.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._published: PublishedIndex | None = None

    # -- reads ---------------------------------------------------------

    @property
    def published(self) -> PublishedIndex | None:
        """The current index, or None. Reading a reference is atomic."""
        return self._published

    def get(self, session: Session) -> PublishedIndex:
        """Return a usable index, building one only when the corpus moved.

        The fast path takes no lock: it reads the version, compares it with
        the published index's, and returns. Only a genuine miss enters the
        critical section, and the section holds no database transaction open
        beyond the build it is performing.
        """
        metrics = get_metrics()
        token = corpus_token(session)

        if token is None:
            # Freshness cannot be proven. Rebuilding every time is the only
            # honest option: reusing an index we cannot validate is exactly
            # the silent staleness this design exists to prevent.
            metrics.increment(SEARCH_INDEX_VERSION_UNAVAILABLE)
            return self._build(session, token="unverified", publish=False)

        current = self._published
        if current is not None and current.version == token:
            metrics.increment(SEARCH_INDEX_REUSE)
            return current

        with self._lock:
            # Re-check: another thread may have published while this one
            # waited. Without this, N simultaneous cold requests would each
            # build a full index.
            current = self._published
            if current is not None and current.version == token:
                metrics.increment(SEARCH_INDEX_CONCURRENT_SUPPRESSED)
                return current

            stale = current
            try:
                fresh = self._build(session, token=token, publish=False)
            except Exception:
                metrics.increment(SEARCH_INDEX_BUILD_FAILURES)
                logger.warning("search_index_build_failed", exc_info=True)
                if stale is not None:
                    # A failed rebuild must never replace a known-good index
                    # with a broken or empty one. Serving the old one is a
                    # judgement call and it is made LOUDLY: the corpus moved,
                    # so this index is known-old, and the counter says so.
                    # The staleness is bounded to descriptive text - no
                    # academic decision comes from search.
                    metrics.increment(SEARCH_INDEX_STALE_SERVED)
                    logger.warning(
                        "search_index_serving_stale",
                        extra={"published_version": stale.version,
                               "wanted_version": token},
                    )
                    return stale
                raise IndexUnavailable("no usable search index") from None

            # Atomic publication: build fully, validate, then swap. The live
            # index is never cleared first.
            self._published = fresh
            return fresh

    # -- build ---------------------------------------------------------

    def _build(self, session: Session, *, token: str, publish: bool) -> PublishedIndex:
        metrics = get_metrics()
        started = time.perf_counter()

        documents = build_course_documents(session)
        searcher = build_bm25(documents)
        elapsed = (time.perf_counter() - started) * 1000

        index = PublishedIndex(
            searcher=searcher,
            documents_by_key={d.course_key: d for d in documents},
            version=token,
            document_count=len(documents),
            build_ms=elapsed,
        )

        metrics.increment(SEARCH_INDEX_BUILDS)
        metrics.observe(SEARCH_INDEX_BUILD_DURATION, elapsed)
        logger.info(
            "search_index_built",
            extra={"documents": len(documents), "build_ms": round(elapsed, 1),
                   "version": token},
        )
        if publish:
            self._published = index
        return index

    # -- test / operational hooks --------------------------------------

    def reset(self) -> None:
        """Drop the published index. Test hook; not called by the app.

        Safe at any time precisely because the index is derived: the next
        request rebuilds it and nothing academic depends on it.
        """
        with self._lock:
            self._published = None


_registry = SearchIndexRegistry()


def get_search_index_registry() -> SearchIndexRegistry:
    return _registry


def get_searcher(session: Session):
    """A searcher for this request, around the shared immutable index.

    The `ExpandingSearcher` is per-call and the `BM25Searcher` inside it is
    shared - see the module docstring for why that split is not arbitrary.
    """
    from app.services.search.synonyms import ExpandingSearcher

    index = _registry.get(session)
    return ExpandingSearcher(index.searcher), index


__all__ = [
    "CORPUS_CODE_VERSION",
    "IndexUnavailable",
    "PublishedIndex",
    "SearchIndexRegistry",
    "corpus_token",
    "get_search_index_registry",
    "get_searcher",
    "read_search_version",
]
