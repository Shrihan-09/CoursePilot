"""Validator for normalized SAS Core records.

Catches what a per-record type check cannot: duplicate goals, eligibility
pointing at goals nobody defined, cross-year mixing, and duplicate
certifications.

Rejections name the record, the field, and the expectation.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from coursepilot_ingestion.core_schemas import (
    NormalizedCoreEligibility,
    NormalizedCoreGoal,
)

logger = logging.getLogger(__name__)


class Level(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(slots=True)
class CoreIssue:
    level: Level
    subject: str
    field_name: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level.value}] {self.subject} {self.field_name}: {self.message}"


@dataclass(slots=True)
class CoreValidationOutcome:
    goals: list[NormalizedCoreGoal] = field(default_factory=list)
    eligibility: list[NormalizedCoreEligibility] = field(default_factory=list)
    rejected: list[CoreIssue] = field(default_factory=list)
    warnings: list[CoreIssue] = field(default_factory=list)
    # SOC codes with no curated goal - ITR, WC, SOEHS, GVT, ECN, CE.
    unmapped_codes: Counter = field(default_factory=Counter)
    duplicate_eligibility: int = 0

    @property
    def error_messages(self) -> list[str]:
        return [str(i) for i in self.rejected]


class CoreValidator:
    """Validates goals and eligibility together.

    Together rather than separately, because the most valuable check is the
    cross-reference: eligibility that names a goal nobody defined.
    """

    def validate(
        self,
        goals: list[NormalizedCoreGoal],
        eligibility: list[NormalizedCoreEligibility],
        catalog_year: str,
    ) -> CoreValidationOutcome:
        outcome = CoreValidationOutcome()

        # --- goals ---
        seen: dict[str, NormalizedCoreGoal] = {}
        for goal in goals:
            if goal.catalog_year != catalog_year:
                outcome.rejected.append(
                    CoreIssue(
                        Level.ERROR,
                        goal.code,
                        "catalog_year",
                        f"is {goal.catalog_year!r} but this run is for {catalog_year!r}; "
                        "catalog years must never be mixed",
                    )
                )
                continue
            if goal.match_key in seen:
                outcome.rejected.append(
                    CoreIssue(
                        Level.ERROR,
                        goal.code,
                        "code",
                        f"duplicate goal for catalog year {catalog_year} "
                        f"(already defined as {seen[goal.match_key].code!r})",
                    )
                )
                continue
            if not goal.description:
                outcome.warnings.append(
                    CoreIssue(Level.WARNING, goal.code, "description", "is missing")
                )
            seen[goal.match_key] = goal
            outcome.goals.append(goal)

        # --- eligibility ---
        known = set(seen)
        pairs: set[tuple[str, str]] = set()
        for item in eligibility:
            if item.catalog_year != catalog_year:
                outcome.rejected.append(
                    CoreIssue(
                        Level.ERROR,
                        item.course_string,
                        "catalog_year",
                        f"is {item.catalog_year!r} but this run is for {catalog_year!r}",
                    )
                )
                continue

            if item.match_key not in known:
                # Not an error: SOC carries codes from other schools and at
                # least two retired SAS codes. Counted and reported so the
                # decision to exclude them stays visible.
                outcome.unmapped_codes[item.goal_code] += 1
                continue

            key = (item.course_string, item.match_key)
            if key in pairs:
                outcome.duplicate_eligibility += 1
                outcome.warnings.append(
                    CoreIssue(
                        Level.WARNING,
                        item.course_string,
                        "goal_code",
                        f"duplicate certification for {item.goal_code}",
                    )
                )
                continue
            pairs.add(key)
            outcome.eligibility.append(item)

        # --- cross-reference the other way ---
        certified = {e.match_key for e in outcome.eligibility}
        for goal in outcome.goals:
            if goal.match_key not in certified:
                outcome.warnings.append(
                    CoreIssue(
                        Level.WARNING,
                        goal.code,
                        "eligibility",
                        "no course in the ingested terms is certified for this goal",
                    )
                )

        if outcome.rejected:
            logger.warning("rejected %d core record(s)", len(outcome.rejected))
            for msg in outcome.error_messages[:10]:
                logger.warning("  %s", msg)
        return outcome
