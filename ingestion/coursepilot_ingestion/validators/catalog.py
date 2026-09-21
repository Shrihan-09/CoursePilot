"""Validator for normalized catalog course entries.

Pydantic already enforces field shape. This layer catches what a per-record
type check cannot: cross-field consistency and cross-record duplication.

Rejections are loud and name the field, the value, and the expectation.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from coursepilot_ingestion.catalog_schemas import NormalizedCatalogCourse

logger = logging.getLogger(__name__)


class Level(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(slots=True)
class CatalogIssue:
    level: Level
    course_string: str
    field_name: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level.value}] {self.course_string} {self.field_name}: {self.message}"


@dataclass(slots=True)
class CatalogValidationOutcome:
    valid: list[NormalizedCatalogCourse] = field(default_factory=list)
    rejected: list[tuple[NormalizedCatalogCourse, list[CatalogIssue]]] = field(
        default_factory=list
    )
    warnings: list[CatalogIssue] = field(default_factory=list)

    @property
    def error_messages(self) -> list[str]:
        return [str(i) for _, issues in self.rejected for i in issues]


class CatalogValidator:
    """Validates catalog entries individually and as a batch."""

    def _check_one(self, entry: NormalizedCatalogCourse) -> list[CatalogIssue]:
        issues: list[CatalogIssue] = []
        cs = entry.course_string

        if not entry.catalog_year:
            issues.append(
                CatalogIssue(Level.ERROR, cs, "catalog_year", "is missing")
            )

        # A catalog entry with neither a title nor a description carries no
        # information; it is a parsing artifact rather than a course.
        if not entry.title and not entry.description:
            issues.append(
                CatalogIssue(
                    Level.ERROR, cs, "title/description", "both are empty; nothing to store"
                )
            )

        if not entry.description:
            # Expected occasionally; measured at 0% for CS, but the catalog
            # does not guarantee it.
            issues.append(
                CatalogIssue(Level.WARNING, cs, "description", "is missing from the catalog")
            )

        if entry.credits_min is None:
            issues.append(
                CatalogIssue(
                    Level.WARNING,
                    cs,
                    "credits",
                    f"could not be parsed from {entry.credits_raw!r}",
                )
            )

        if entry.title and entry.title == cs:
            issues.append(
                CatalogIssue(
                    Level.WARNING, cs, "title", "is just the course code; title may be missing"
                )
            )

        return issues

    def _check_batch(
        self, entries: list[NormalizedCatalogCourse]
    ) -> list[CatalogIssue]:
        """A course must appear at most once per catalog year.

        The database enforces this too, but catching it here names the
        conflicting values instead of surfacing an opaque IntegrityError.
        """
        issues: list[CatalogIssue] = []
        by_key: dict[tuple[str, str], list[NormalizedCatalogCourse]] = defaultdict(list)
        for e in entries:
            by_key[(e.course_string, e.catalog_year)].append(e)

        for (cs, year), group in by_key.items():
            if len(group) == 1:
                continue
            titles = {g.title for g in group}
            issues.append(
                CatalogIssue(
                    Level.WARNING,
                    cs,
                    "duplicate",
                    f"appears {len(group)} times in catalog year {year}; titles={sorted(t or '' for t in titles)}",
                )
            )
        return issues

    def validate(
        self, entries: list[NormalizedCatalogCourse]
    ) -> CatalogValidationOutcome:
        outcome = CatalogValidationOutcome()

        for entry in entries:
            issues = self._check_one(entry)
            errors = [i for i in issues if i.level is Level.ERROR]
            warns = [i for i in issues if i.level is Level.WARNING]

            if errors:
                outcome.rejected.append((entry, errors))
            else:
                outcome.valid.append(entry)
            outcome.warnings.extend(warns)

        outcome.warnings.extend(self._check_batch(outcome.valid))

        if outcome.rejected:
            logger.warning("rejected %d catalog entr(ies)", len(outcome.rejected))
            for msg in outcome.error_messages[:10]:
                logger.warning("  %s", msg)
        return outcome
