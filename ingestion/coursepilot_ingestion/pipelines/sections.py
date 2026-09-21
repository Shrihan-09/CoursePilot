"""The SOC section ingestion pipeline.

Same shape as the course pipeline, same stage separation:

    fetch -> parse -> normalize -> validate -> load

The fetcher is reused unchanged. Sections live inside the same courses.json
payload, so section ingestion costs no extra network traffic and reads the
same archived bytes - which also means courses and sections ingested from one
archive are guaranteed to be consistent with each other.
"""

from __future__ import annotations

import logging
import pathlib
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from coursepilot_ingestion.fetchers.soc import SocFetcher
from coursepilot_ingestion.loaders.postgres import CourseLoader
from coursepilot_ingestion.loaders.sections import SectionLoader
from coursepilot_ingestion.normalizers.sections import SocSectionNormalizer
from coursepilot_ingestion.parsers.sections import SocSectionParser
from coursepilot_ingestion.section_schemas import NormalizedSection, SectionIngestionStats
from coursepilot_ingestion.sources.soc import SOURCE_KIND, SocQuery
from coursepilot_ingestion.validators.section import SectionValidator

logger = logging.getLogger(__name__)


class SectionIngestionPipeline:
    """End-to-end SOC section ingestion."""

    def __init__(self, session: Session, cache_dir: pathlib.Path) -> None:
        self.session = session
        self.fetcher = SocFetcher(cache_dir)
        self.parser = SocSectionParser()
        self.normalizer_factory = SocSectionNormalizer
        self.validator = SectionValidator()
        self.loader = SectionLoader(session)
        # Reused solely for get_or_create_source, so course and section runs
        # over the same payload share one provenance row instead of creating
        # two rows describing the same bytes.
        self.source_loader = CourseLoader(session)

    def run(
        self,
        query: SocQuery,
        *,
        limit: int | None = None,
        subject_filter: str | None = None,
        use_cache: bool = True,
        commit: bool = True,
    ) -> SectionIngestionStats:
        """Run the pipeline.

        `limit` and `subject_filter` restrict what is LOADED, never what is
        fetched or archived - the raw payload is always kept whole.
        """
        stats = SectionIngestionStats(started_at=datetime.now(UTC))

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
        stats.parsed = len(parsed.sections)
        stats.parse_failed = len(parsed.failures)
        for failure in parsed.failures[:20]:
            stats.errors.append(
                f"parse {failure.course_string}/{failure.section_index}: {failure.error}"
            )

        # --- select the working subset ---
        selected = parsed.sections
        if subject_filter:
            selected = [(p, s) for p, s in selected if p.subject_code == subject_filter]
            logger.info("subject filter %s -> %d sections", subject_filter, len(selected))
        if limit is not None:
            selected = selected[:limit]
            logger.info("limit %d -> %d sections", limit, len(selected))

        # --- normalize ---
        normalizer = self.normalizer_factory(term_code=query.term_code)
        normalized: list[NormalizedSection] = []
        for parent, raw in selected:
            try:
                normalized.append(normalizer.normalize(parent, raw))
            except Exception as exc:  # noqa: BLE001
                # A malformed record must not abort the batch; one bad section
                # should not cost the other 11,991.
                stats.validation_failed += 1
                stats.errors.append(f"normalize {parent.course_string}/{raw.index}: {exc}")

        # --- validate ---
        outcome = self.validator.validate(normalized)
        stats.validated = len(outcome.valid)
        stats.validation_failed += len(outcome.rejected)
        stats.errors.extend(outcome.error_messages[:20])
        stats.warnings.extend(outcome.warning_messages[:50])

        for warning in outcome.warnings[:10]:
            logger.info("warning: %s", warning)
        if len(outcome.warnings) > 10:
            logger.info("... and %d more warnings", len(outcome.warnings) - 10)

        # --- load ---
        source = self.source_loader.get_or_create_source(
            kind=SOURCE_KIND,
            url=fetched.url,
            content_hash=fetched.content_hash,
            retrieved_at=fetched.retrieved_at,
            term_code=query.term_code,
            academic_year=str(query.year),
            archive_path=str(fetched.archive_path) if fetched.archive_path else None,
            record_count=parsed.total,
        )
        self.loader.load(outcome.valid, source.id, stats, term_code=query.term_code)

        if commit:
            self.session.commit()
        else:
            self.session.rollback()

        stats.finished_at = datetime.now(UTC)
        logger.info("section ingestion complete: %s", stats.summary())

        if stats.unmatched_offering:
            # Surfaced at WARNING because it usually means courses were
            # ingested with --limit/--subject and the section payload covers
            # courses that were never loaded. Not an error, but never silent.
            logger.warning(
                "%d section(s) had no matching course offering; "
                "ingest the corresponding courses first",
                stats.unmatched_offering,
            )
        return stats
