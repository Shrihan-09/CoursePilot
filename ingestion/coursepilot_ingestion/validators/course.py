"""Validator: the last gate before the database.

Pydantic already enforces types and field-level rules on `NormalizedCourse`.
This layer catches what a single-record type check cannot:

  * cross-field consistency (does course_string agree with its parts?)
  * cross-record consistency (does the batch contain conflicting duplicates?)
  * suspicious-but-legal values worth flagging without rejecting

Design rule: **rejections are loud and specific.** A validator that says
"invalid course" is nearly useless at 4,400 records; one that names the field,
the value, and the expectation is debuggable.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from coursepilot_ingestion.schemas import NormalizedCourse

logger = logging.getLogger(__name__)


class Level(StrEnum):
    ERROR = "error"      # reject the record
    WARNING = "warning"  # accept, but say something


@dataclass(slots=True)
class Issue:
    level: Level
    course_string: str
    field_name: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level.value}] {self.course_string} {self.field_name}: {self.message}"


@dataclass(slots=True)
class ValidationOutcome:
    valid: list[NormalizedCourse] = field(default_factory=list)
    rejected: list[tuple[NormalizedCourse, list[Issue]]] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def error_messages(self) -> list[str]:
        return [str(i) for _, issues in self.rejected for i in issues]


class CourseValidator:
    """Validates normalized courses, individually and as a batch."""

    # Observed maximum in real data is 16. Anything far above that is a parse
    # error rather than an unusual course, but we warn instead of rejecting —
    # the observed maximum is not a documented Rutgers limit.
    PLAUSIBLE_MAX_CREDITS = 16

    def _check_one(self, course: NormalizedCourse) -> list[Issue]:
        issues: list[Issue] = []
        cs = course.course_string

        # course_string must agree with the parts we key on. If these ever
        # disagree, one of them is wrong and we cannot tell which — so reject
        # rather than pick a winner.
        expected = f"{course.offering_unit_code}:{course.subject_code}:{course.course_number}"
        if cs != expected:
            issues.append(
                Issue(
                    Level.ERROR,
                    cs,
                    "course_string",
                    f"disagrees with its components (expected {expected!r})",
                )
            )

        if not course.title.strip():
            issues.append(Issue(Level.ERROR, cs, "title", "is empty"))

        # A title identical to the course string means we captured no real
        # title. Legal, but almost certainly an upstream problem.
        if course.title.strip() == cs:
            issues.append(
                Issue(Level.WARNING, cs, "title", "is just the course string; title may be missing")
            )

        if course.credits is None:
            # Expected for ~11% of records. Worth surfacing, not rejecting:
            # a course with no credit value is still a real course.
            issues.append(
                Issue(Level.WARNING, cs, "credits", "is null (SOC omits credits for some courses)")
            )
        elif course.credits > self.PLAUSIBLE_MAX_CREDITS:
            issues.append(
                Issue(
                    Level.WARNING,
                    cs,
                    "credits",
                    f"{course.credits} exceeds the observed maximum of {self.PLAUSIBLE_MAX_CREDITS}",
                )
            )

        if course.level is None:
            issues.append(Issue(Level.WARNING, cs, "level", "is missing"))

        return issues

    def _check_batch(self, courses: list[NormalizedCourse]) -> list[Issue]:
        """Cross-record checks.

        The important one: the SOC payload legitimately contains the same
        course twice when it runs at two campuses. Those rows must agree on
        course-level attributes — if they disagree on credits or title, our
        assumption that campus belongs to the offering is wrong for that
        record, and we need to know rather than silently keep whichever row
        happened to be processed last.
        """
        issues: list[Issue] = []
        by_key: dict[tuple, list[NormalizedCourse]] = defaultdict(list)
        for c in courses:
            by_key[c.natural_key].append(c)

        for key, group in by_key.items():
            if len(group) == 1:
                continue
            cs = group[0].course_string
            titles = {c.title for c in group}
            credits = {c.credits for c in group}
            campuses = sorted({c.campus_code for c in group})

            if len(titles) > 1:
                issues.append(
                    Issue(
                        Level.WARNING,
                        cs,
                        "title",
                        f"duplicate natural key {key} disagrees on title across campuses "
                        f"{campuses}: {sorted(titles)}",
                    )
                )
            if len(credits) > 1:
                issues.append(
                    Issue(
                        Level.WARNING,
                        cs,
                        "credits",
                        f"duplicate natural key {key} disagrees on credits across campuses "
                        f"{campuses}: {sorted(str(x) for x in credits)}",
                    )
                )
        return issues

    def validate(self, courses: list[NormalizedCourse]) -> ValidationOutcome:
        outcome = ValidationOutcome()

        for course in courses:
            issues = self._check_one(course)
            errors = [i for i in issues if i.level is Level.ERROR]
            warns = [i for i in issues if i.level is Level.WARNING]

            if errors:
                outcome.rejected.append((course, errors))
            else:
                outcome.valid.append(course)
            outcome.warnings.extend(warns)

        outcome.warnings.extend(self._check_batch(outcome.valid))

        if outcome.rejected:
            logger.warning("rejected %d course(s) during validation", len(outcome.rejected))
            for msg in outcome.error_messages[:10]:
                logger.warning("  %s", msg)
        return outcome
