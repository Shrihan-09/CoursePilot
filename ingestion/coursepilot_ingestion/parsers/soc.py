"""Parser: raw bytes in, `RawSocCourse` objects out.

Structural extraction only — no business meaning is applied here. The parser's
one job is to turn an opaque payload into typed objects, and to report exactly
which records it could not handle.

It never repairs data. A record that does not fit the expected shape is
reported as a failure, not quietly coerced: a silently "fixed" course becomes
a wrong answer later, with no trace of where it went wrong.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from pydantic import ValidationError

from coursepilot_ingestion.schemas import RawSocCourse

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ParseFailure:
    index: int
    course_string: str | None
    error: str


@dataclass(slots=True)
class ParseResult:
    courses: list[RawSocCourse] = field(default_factory=list)
    failures: list[ParseFailure] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.courses) + len(self.failures)


class SocParser:
    """Parses the SOC `courses.json` payload."""

    def parse(self, content: bytes) -> ParseResult:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            # Unparseable payload is fatal, not a per-record failure — there
            # are no records to attribute it to.
            raise ValueError(f"SOC payload is not valid JSON: {exc}") from exc

        if not isinstance(data, list):
            raise ValueError(
                f"expected the SOC payload to be a JSON array of courses, got {type(data).__name__}"
            )

        result = ParseResult()
        for i, raw in enumerate(data):
            if not isinstance(raw, dict):
                result.failures.append(
                    ParseFailure(index=i, course_string=None, error=f"expected object, got {type(raw).__name__}")
                )
                continue
            try:
                result.courses.append(RawSocCourse.model_validate(raw))
            except ValidationError as exc:
                result.failures.append(
                    ParseFailure(
                        index=i,
                        course_string=raw.get("courseString"),
                        # Compact: the full Pydantic error is long, and at
                        # 4,400 records a verbose log buries the signal.
                        error="; ".join(
                            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                            for e in exc.errors()[:3]
                        ),
                    )
                )

        if result.failures:
            logger.warning(
                "parsed %d/%d SOC courses (%d failed)",
                len(result.courses),
                result.total,
                len(result.failures),
            )
        return result
