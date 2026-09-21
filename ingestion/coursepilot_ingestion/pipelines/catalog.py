"""The catalog course-description ingestion pipeline.

    fetch -> parse -> normalize -> validate -> load

Wires the stages and owns the transaction. Contains no HTTP, no parsing, and
no SQL of its own - each stage stays independently testable.
"""

from __future__ import annotations

import logging
import pathlib

from sqlalchemy.orm import Session

from coursepilot_ingestion.catalog_schemas import (
    CatalogIngestionStats,
    NormalizedCatalogCourse,
)
from coursepilot_ingestion.fetchers.catalog import CatalogFetcher
from coursepilot_ingestion.loaders.catalog import CatalogLoader
from coursepilot_ingestion.normalizers.catalog import CatalogNormalizer
from coursepilot_ingestion.parsers.catalog import CatalogParser
from coursepilot_ingestion.sources.catalog import SOURCE_KIND, CatalogQuery
from coursepilot_ingestion.validators.catalog import CatalogValidator

logger = logging.getLogger(__name__)


class CatalogIngestionPipeline:
    """End-to-end catalog course-entry ingestion for one catalog year."""

    def __init__(self, session: Session, cache_dir: pathlib.Path) -> None:
        self.session = session
        self.fetcher = CatalogFetcher(cache_dir)
        self.parser = CatalogParser()
        self.validator = CatalogValidator()
        self.loader = CatalogLoader(session)

    def run(
        self,
        query: CatalogQuery,
        *,
        use_cache: bool = True,
        commit: bool = True,
    ) -> CatalogIngestionStats:
        stats = CatalogIngestionStats(catalog_year=query.catalog_year)

        # --- fetch ---
        fetched = self.fetcher.fetch(query, use_cache=use_cache)
        stats.source_content_hash = fetched.content_hash
        logger.info(
            "fetched %s bytes (cache=%s) hash=%s",
            f"{fetched.size_bytes:,}",
            fetched.from_cache,
            fetched.content_hash[:12],
        )

        # --- parse ---
        parsed = self.parser.parse(fetched.html)
        stats.parsed = len(parsed.courses)
        stats.parse_failed = len(parsed.failures)
        for failure in parsed.failures[:20]:
            stats.errors.append(f"parse {failure.course_string}: {failure.error}")

        # --- normalize ---
        normalizer = CatalogNormalizer(
            catalog_year=query.catalog_year, source_url=fetched.url
        )
        normalized: list[NormalizedCatalogCourse] = []
        for raw in parsed.courses:
            try:
                normalized.append(normalizer.normalize(raw))
            except Exception as exc:  # noqa: BLE001
                # One bad record must not abort the batch.
                stats.validation_failed += 1
                stats.errors.append(f"normalize {raw.course_string}: {exc}")

        # --- validate ---
        outcome = self.validator.validate(normalized)
        stats.validated = len(outcome.valid)
        stats.validation_failed += len(outcome.rejected)
        stats.errors.extend(outcome.error_messages[:20])

        for warning in outcome.warnings[:10]:
            logger.info("warning: %s", warning)
        if len(outcome.warnings) > 10:
            logger.info("... and %d more warnings", len(outcome.warnings) - 10)

        # --- load ---
        source = self.loader.get_or_create_source(
            kind=SOURCE_KIND,
            url=fetched.url,
            content_hash=fetched.content_hash,
            retrieved_at=fetched.retrieved_at,
            catalog_year=query.catalog_year,
            archive_path=str(fetched.archive_path) if fetched.archive_path else None,
            record_count=parsed.total,
        )
        self.loader.load(outcome.valid, source, stats)

        if commit:
            self.session.commit()
        else:
            self.session.rollback()

        logger.info("catalog ingestion complete: %s", stats.summary())
        return stats
