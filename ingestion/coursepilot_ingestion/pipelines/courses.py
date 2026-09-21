"""The SOC course ingestion pipeline.

Wires the stages together and owns the transaction. Each stage stays ignorant
of the others, which is what makes them individually testable:

    fetch -> parse -> normalize -> validate -> load

The pipeline itself contains no parsing, no HTTP, and no SQL.
"""

from __future__ import annotations

import logging
import pathlib
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from coursepilot_ingestion.fetchers.soc import SocFetcher
from coursepilot_ingestion.loaders.postgres import CourseLoader
from coursepilot_ingestion.normalizers.soc import SocNormalizer
from coursepilot_ingestion.parsers.soc import SocParser
from coursepilot_ingestion.schemas import IngestionStats, NormalizedCourse
from coursepilot_ingestion.sources.soc import SOURCE_KIND, SocQuery
from coursepilot_ingestion.validators.course import CourseValidator

logger = logging.getLogger(__name__)


class CourseIngestionPipeline:
    """End-to-end SOC course ingestion."""

    def __init__(self, session: Session, cache_dir: pathlib.Path) -> None:
        self.session = session
        self.fetcher = SocFetcher(cache_dir)
        self.parser = SocParser()
        self.validator = CourseValidator()
        self.loader = CourseLoader(session)

    def run(
        self,
        query: SocQuery,
        *,
        limit: int | None = None,
        subject_filter: str | None = None,
        use_cache: bool = True,
        commit: bool = True,
    ) -> IngestionStats:
        """Run the pipeline.

        `limit` and `subject_filter` exist so a prototype run stays small
        enough to inspect by hand. They restrict what is LOADED, never what is
        fetched or archived - the raw payload is always kept whole, so a
        narrow run does not produce a truncated archive that a later, wider
        run would silently trust.
        """
        stats = IngestionStats(started_at=datetime.now(UTC))

        # --- fetch ---
        fetched = self.fetcher.fetch_courses(query, use_cache=use_cache)
        stats.source_content_hash = fetched.content_hash
        logger.info(
            "fetched %s bytes (cache=%s) hash=%s",
            f"{fetched.size_bytes:,}",
            fetched.from_cache,
            fetched.content_hash[:12],
        )

        # --- parse ---
        parsed = self.parser.parse(fetched.content)
        stats.fetched = parsed.total
        stats.parsed = len(parsed.courses)
        stats.parse_failed = len(parsed.failures)
        for failure in parsed.failures[:20]:
            stats.errors.append(f"parse[{failure.index}] {failure.course_string}: {failure.error}")

        # --- select the working subset ---
        selected = parsed.courses
        if subject_filter:
            selected = [c for c in selected if c.subject == subject_filter]
            logger.info("subject filter %s -> %d courses", subject_filter, len(selected))
        if limit is not None:
            selected = selected[:limit]
            logger.info("limit %d -> %d courses", limit, len(selected))

        # --- normalize ---
        normalizer = SocNormalizer(term_code=query.term_code)
        normalized: list[NormalizedCourse] = []
        for raw in selected:
            try:
                normalized.append(normalizer.normalize(raw))
            except Exception as exc:  # noqa: BLE001
                # Normalization failure is a data problem, not a crash. Record
                # it and keep going, so one bad record does not abort 4,399
                # good ones.
                stats.validation_failed += 1
                stats.errors.append(f"normalize {raw.courseString}: {exc}")

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
            term_code=query.term_code,
            academic_year=str(query.year),
            archive_path=str(fetched.archive_path) if fetched.archive_path else None,
            record_count=parsed.total,
        )
        self.loader.load(outcome.valid, source, stats)

        if commit:
            self.session.commit()
        else:
            # Used by tests that want to inspect state without persisting.
            self.session.rollback()

        stats.finished_at = datetime.now(UTC)
        logger.info("ingestion complete: %s", stats.summary())
        return stats
