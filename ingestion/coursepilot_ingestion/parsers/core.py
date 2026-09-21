"""Parser for SAS Core data.

Two parsers, because Core's two halves come from two sources:

  * `CoreDefinitionParser` - the curated goal/structure JSON (SAS OUE prose)
  * `CoreEligibilityParser` - `coreCodes` inside the archived SOC payload

Structural extraction only. Nothing is repaired; a record that does not fit
the expected shape is reported as a failure.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from pydantic import ValidationError

from coursepilot_ingestion.core_schemas import RawCoreCode, RawCoreGoal

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CoreParseFailure:
    subject: str | None
    error: str


@dataclass(slots=True)
class CoreDefinitionParseResult:
    goals: list[RawCoreGoal] = field(default_factory=list)
    requirements: list[dict] = field(default_factory=list)
    target_program: dict = field(default_factory=dict)
    program_version: dict = field(default_factory=dict)
    school: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)
    known_conflicts: list[dict] = field(default_factory=list)
    failures: list[CoreParseFailure] = field(default_factory=list)


@dataclass(slots=True)
class CoreEligibilityParseResult:
    """(course_string, RawCoreCode) pairs.

    A pair rather than a nested structure because the course is the enclosing
    object in SOC and the code carries no course identity of its own - the
    same structural problem as sections.
    """

    entries: list[tuple[str, RawCoreCode]] = field(default_factory=list)
    failures: list[CoreParseFailure] = field(default_factory=list)
    courses_with_codes: int = 0

    @property
    def total(self) -> int:
        return len(self.entries) + len(self.failures)


class CoreDefinitionParser:
    """Parses the curated SAS Core definition."""

    def parse(self, content: bytes) -> CoreDefinitionParseResult:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"core definition is not valid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(
                f"expected the core definition to be an object, got {type(data).__name__}"
            )
        for required in ("source", "target_program", "program_version", "goals", "requirements"):
            if required not in data:
                raise ValueError(f"core definition is missing required key {required!r}")

        result = CoreDefinitionParseResult(
            requirements=data["requirements"],
            target_program=data["target_program"],
            program_version=data["program_version"],
            school=data.get("school", {}),
            source=data["source"],
            known_conflicts=data.get("known_conflicts", []),
        )

        for raw in data["goals"]:
            try:
                result.goals.append(RawCoreGoal.model_validate(raw))
            except ValidationError as exc:
                result.failures.append(
                    CoreParseFailure(
                        subject=str(raw.get("code")),
                        error="; ".join(e["msg"] for e in exc.errors()[:3]),
                    )
                )
        return result


class CoreEligibilityParser:
    """Extracts `coreCodes` from an archived SOC courses payload."""

    def parse(self, content: bytes) -> CoreEligibilityParseResult:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"SOC payload is not valid JSON: {exc}") from exc

        if not isinstance(data, list):
            raise ValueError(
                f"expected the SOC payload to be a JSON array, got {type(data).__name__}"
            )

        result = CoreEligibilityParseResult()
        for course in data:
            if not isinstance(course, dict):
                continue
            codes = course.get("coreCodes") or []
            if not codes:
                continue

            course_string = course.get("courseString")
            if not course_string:
                result.failures.append(
                    CoreParseFailure(subject=None, error="course has coreCodes but no courseString")
                )
                continue

            result.courses_with_codes += 1
            for raw in codes:
                try:
                    result.entries.append(
                        (course_string, RawCoreCode.model_validate(raw))
                    )
                except ValidationError as exc:
                    result.failures.append(
                        CoreParseFailure(
                            subject=f"{course_string}:{raw.get('code')}",
                            error="; ".join(e["msg"] for e in exc.errors()[:3]),
                        )
                    )

        if result.failures:
            logger.warning(
                "parsed %d/%d core code entries (%d failed)",
                len(result.entries),
                result.total,
                len(result.failures),
            )
        return result
