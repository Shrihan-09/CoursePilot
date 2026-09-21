"""Normalizer: raw SAS Core records -> CoursePilot vocabulary.

The one transformation worth naming: **goal codes are matched
case-insensitively**. The official SAS page writes WCR/WCD; SOC writes
WCr/WCd. They are the same two goals, and treating them as different would
leave both requirements permanently unsatisfiable while looking correct.

The CURATED spelling is kept as the stored code, because the official page is
authoritative for goal identity. SOC is authoritative only for which courses
are certified.
"""

from __future__ import annotations

import logging

from coursepilot_ingestion.core_schemas import (
    NormalizedCoreEligibility,
    NormalizedCoreGoal,
    RawCoreCode,
    RawCoreGoal,
)

logger = logging.getLogger(__name__)


class CoreNormalizer:
    def __init__(self, catalog_year: str, observed_term_code: str | None = None) -> None:
        self.catalog_year = catalog_year
        self.observed_term_code = observed_term_code

    def normalize_goal(self, raw: RawCoreGoal) -> NormalizedCoreGoal:
        description = (raw.description or "").strip()
        return NormalizedCoreGoal(
            code=raw.code.strip(),
            name=raw.name.strip(),
            description=description or None,
            catalog_year=self.catalog_year,
        )

    def normalize_eligibility(
        self, course_string: str, raw: RawCoreCode
    ) -> NormalizedCoreEligibility:
        return NormalizedCoreEligibility(
            course_string=course_string.strip(),
            goal_code=raw.code.strip(),
            catalog_year=self.catalog_year,
            observed_term_code=self.observed_term_code,
        )
