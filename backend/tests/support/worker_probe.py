"""A real worker process for cross-process verification (Phase 5.12, Part 7).

Run as a subprocess so the BM25 registry really is in a different address
space - an in-process thread would share the registry and prove nothing
about the property under test.

    python -m tests.support.worker_probe <command> [args...]

Emits one JSON object on stdout so the parent can assert on facts rather
than parse prose.
"""

from __future__ import annotations

import json
import os
import sys
import time


def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    engine = create_engine(os.environ["DATABASE_URL_SYNC"], poolclass=NullPool)
    return sessionmaker(engine)(), engine


def cmd_build_and_report() -> dict:
    """Build the index in this process and report what it saw."""
    from app.core.metrics import SEARCH_INDEX_BUILDS, SEARCH_INDEX_REUSE, get_metrics
    from app.services.search.index_registry import SearchIndexRegistry, corpus_token

    session, engine = _session()
    try:
        registry = SearchIndexRegistry()
        token_before = corpus_token(session)
        first = registry.get(session)
        second = registry.get(session)            # must reuse
        return {
            "pid": os.getpid(),
            "token": token_before,
            "index_version": first.version,
            "documents": first.document_count,
            "reused": second is first,
            "builds": get_metrics().counter(SEARCH_INDEX_BUILDS),
            "reuse": get_metrics().counter(SEARCH_INDEX_REUSE),
            "build_ms": round(first.build_ms, 1),
        }
    finally:
        session.close()
        engine.dispose()


def cmd_detect_change(marker: str) -> dict:
    """Build, wait for the parent to mutate, then observe the new version."""
    from app.services.search.index_registry import SearchIndexRegistry
    from app.services.search.synonyms import ExpandingSearcher

    session, engine = _session()
    try:
        registry = SearchIndexRegistry()
        before = registry.get(session)
        before_hits = [
            r.course_key
            for r in ExpandingSearcher(before.searcher).search(marker, limit=5)
        ]

        # Signal readiness and wait for the parent's mutation.
        print(json.dumps({"ready": True, "pid": os.getpid()}), flush=True)
        sys.stdin.readline()

        after = registry.get(session)
        after_hits = [
            r.course_key
            for r in ExpandingSearcher(after.searcher).search(marker, limit=5)
        ]
        return {
            "pid": os.getpid(),
            "before_version": before.version,
            "after_version": after.version,
            "rebuilt": after is not before,
            "before_hits": before_hits,
            "after_hits": after_hits,
        }
    finally:
        session.close()
        engine.dispose()


def cmd_simultaneous_build(start_at: float) -> dict:
    """Build at a wall-clock instant shared with a sibling process."""
    from app.services.search.index_registry import SearchIndexRegistry
    from app.services.search.synonyms import ExpandingSearcher

    session, engine = _session()
    try:
        registry = SearchIndexRegistry()
        while time.time() < start_at:
            time.sleep(0.005)
        index = registry.get(session)
        ranking = [
            r.course_key
            for r in ExpandingSearcher(index.searcher).search("data structures",
                                                              limit=5)
        ]
        return {
            "pid": os.getpid(),
            "version": index.version,
            "documents": index.document_count,
            "ranking": ranking,
        }
    finally:
        session.close()
        engine.dispose()


def main(argv: list[str]) -> int:
    os.environ.setdefault("COURSEPILOT_ENV", "ci")
    command = argv[1]
    if command == "build":
        result = cmd_build_and_report()
    elif command == "detect":
        result = cmd_detect_change(argv[2])
    elif command == "simultaneous":
        result = cmd_simultaneous_build(float(argv[2]))
    else:                                          # pragma: no cover
        print(json.dumps({"error": f"unknown command {command}"}))
        return 2
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
