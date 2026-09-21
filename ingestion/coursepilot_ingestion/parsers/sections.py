"""Parser: raw SOC bytes -> (parent course identity, RawSocSection) pairs.

Sections are NESTED inside course objects in courses.json - a section carries
no field naming its own course. The parent is structural, so this parser must
walk courses and carry the parent's identity down with each section.

Like the course parser, this one never repairs data. A section that does not
fit the expected shape is reported, not coerced.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from pydantic import ValidationError

from coursepilot_ingestion.section_schemas import RawSocSection

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParentCourseRef:
    """The identity of the course a section was nested under.

    Carried explicitly rather than re-derived later, because once a section is
    detached from its position in the payload the linkage is unrecoverable.
    """

    course_string: str
    offering_unit_code: str
    subject_code: str
    course_number: str
    supplement_code: str
    campus_code: str


@dataclass(slots=True)
class SectionParseFailure:
    course_string: str | None
    section_index: str | None
    error: str


@dataclass(slots=True)
class SectionParseResult:
    sections: list[tuple[ParentCourseRef, RawSocSection]] = field(default_factory=list)
    failures: list[SectionParseFailure] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.sections) + len(self.failures)


class SocSectionParser:
    """Extracts sections from the SOC courses.json payload."""

    def parse(self, content: bytes) -> SectionParseResult:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"SOC payload is not valid JSON: {exc}") from exc

        if not isinstance(data, list):
            raise ValueError(
                f"expected the SOC payload to be a JSON array of courses, got {type(data).__name__}"
            )

        result = SectionParseResult()

        for raw_course in data:
            if not isinstance(raw_course, dict):
                continue

            course_string = raw_course.get("courseString")
            # A section whose parent has no usable identity cannot be linked to
            # an offering later, so it is reported rather than parsed into a
            # record we could never attach.
            required = ("offeringUnitCode", "subject", "courseNumber")
            if not all(raw_course.get(k) for k in required):
                for raw_section in raw_course.get("sections") or []:
                    result.failures.append(
                        SectionParseFailure(
                            course_string=course_string,
                            section_index=(raw_section or {}).get("index"),
                            error="parent course is missing natural-key fields "
                            f"({', '.join(k for k in required if not raw_course.get(k))})",
                        )
                    )
                continue

            parent = ParentCourseRef(
                course_string=course_string or "",
                offering_unit_code=raw_course["offeringUnitCode"],
                subject_code=raw_course["subject"],
                course_number=raw_course["courseNumber"],
                # SOC sends two spaces for "no supplement"; normalize here so
                # the value matches the Course natural key exactly.
                supplement_code=(raw_course.get("supplementCode") or "").strip(),
                campus_code=(raw_course.get("campusCode") or "").strip(),
            )

            for raw_section in raw_course.get("sections") or []:
                if not isinstance(raw_section, dict):
                    result.failures.append(
                        SectionParseFailure(
                            course_string=course_string,
                            section_index=None,
                            error=f"expected section object, got {type(raw_section).__name__}",
                        )
                    )
                    continue
                try:
                    result.sections.append(
                        (parent, RawSocSection.model_validate(raw_section))
                    )
                except ValidationError as exc:
                    result.failures.append(
                        SectionParseFailure(
                            course_string=course_string,
                            section_index=raw_section.get("index"),
                            error="; ".join(
                                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                                for e in exc.errors()[:3]
                            ),
                        )
                    )

        if result.failures:
            logger.warning(
                "parsed %d/%d SOC sections (%d failed)",
                len(result.sections),
                result.total,
                len(result.failures),
            )
        return result
