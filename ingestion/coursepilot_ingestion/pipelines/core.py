"""The SAS Core Curriculum ingestion pipeline.

    fetch -> archive -> parse -> normalize -> validate -> load

Unusual in one respect: it has TWO inputs, because Core's two halves have
different authoritative sources.

    curated definition  (SAS OUE prose)  -> goals, areas, credit/course counts
    archived SOC payload (coreCodes)     -> which courses are certified

Both are read from archives, so the pipeline is reproducible offline and no
test needs the Rutgers network.
"""

from __future__ import annotations

import hashlib
import logging
import pathlib

from sqlalchemy.orm import Session

from coursepilot_ingestion.core_schemas import (
    CoreIngestionStats,
    NormalizedCoreEligibility,
    NormalizedCoreGoal,
)
from coursepilot_ingestion.loaders.core import CoreLoader
from coursepilot_ingestion.normalizers.core import CoreNormalizer
from coursepilot_ingestion.parsers.core import CoreDefinitionParser, CoreEligibilityParser
from coursepilot_ingestion.validators.core import CoreValidator

logger = logging.getLogger(__name__)


class CoreIngestionPipeline:
    """End-to-end SAS Core ingestion for one catalog year."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.definition_parser = CoreDefinitionParser()
        self.eligibility_parser = CoreEligibilityParser()
        self.validator = CoreValidator()
        self.loader = CoreLoader(session)

    def run(
        self,
        definition_path: pathlib.Path,
        soc_archive_path: pathlib.Path,
        *,
        observed_term_code: str | None = None,
        commit: bool = True,
    ) -> CoreIngestionStats:
        raw_definition = definition_path.read_bytes()
        definition = self.definition_parser.parse(raw_definition)
        catalog_year = definition.source["catalog_year"]

        stats = CoreIngestionStats(catalog_year=catalog_year)
        stats.source_content_hash = hashlib.sha256(raw_definition).hexdigest()

        for failure in definition.failures:
            stats.errors.append(f"goal {failure.subject}: {failure.error}")

        # --- eligibility from the archived SOC payload ---
        if not soc_archive_path.exists():
            raise FileNotFoundError(
                f"SOC archive not found: {soc_archive_path}. Core eligibility comes from "
                "SOC coreCodes; run the course pipeline first."
            )
        parsed_eligibility = self.eligibility_parser.parse(soc_archive_path.read_bytes())
        stats.core_codes_seen = len(parsed_eligibility.entries)
        for failure in parsed_eligibility.failures[:20]:
            stats.errors.append(f"coreCode {failure.subject}: {failure.error}")

        # --- normalize ---
        normalizer = CoreNormalizer(
            catalog_year=catalog_year, observed_term_code=observed_term_code
        )
        goals: list[NormalizedCoreGoal] = []
        for raw_goal in definition.goals:
            try:
                goals.append(normalizer.normalize_goal(raw_goal))
            except Exception as exc:  # noqa: BLE001
                stats.errors.append(f"normalize goal {raw_goal.code}: {exc}")

        eligibility: list[NormalizedCoreEligibility] = []
        for course_string, raw_code in parsed_eligibility.entries:
            try:
                eligibility.append(normalizer.normalize_eligibility(course_string, raw_code))
            except Exception as exc:  # noqa: BLE001
                # One malformed certification must not abort the batch.
                stats.errors.append(f"normalize {course_string}/{raw_code.code}: {exc}")
        stats.eligibility_parsed = len(eligibility)

        # --- validate ---
        outcome = self.validator.validate(goals, eligibility, catalog_year)
        stats.eligibility_duplicates = outcome.duplicate_eligibility
        stats.unmapped_goal_codes = dict(outcome.unmapped_codes)
        stats.errors.extend(outcome.error_messages[:20])

        for warning in outcome.warnings[:10]:
            logger.info("warning: %s", warning)

        # --- load ---
        self.loader.load(
            definition,
            outcome.goals,
            outcome.eligibility,
            raw_definition,
            catalog_year,
            stats,
        )

        if commit:
            self.session.commit()
        else:
            self.session.rollback()

        logger.info("core ingestion complete: %s", stats.summary())
        return stats
