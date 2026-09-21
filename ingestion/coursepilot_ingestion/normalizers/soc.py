"""Normalizer: `RawSocCourse` in, `NormalizedCourse` out.

This is the boundary between Rutgers' vocabulary and CoursePilot's. Every
transformation here is a decision, and each one is justified against measured
data (see docs/DATA_SOURCES.md).

Normalization does NOT invent values. Where SOC gives us nothing, the result
is `None` — never a guess. An invented default is indistinguishable from a
real value once it is in the database.
"""

from __future__ import annotations

import html
import logging
import re

from coursepilot_ingestion.schemas import NormalizedCourse, RawSocCourse

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_text(value: str | None) -> str | None:
    """Strip HTML tags, unescape entities, collapse whitespace.

    SOC embeds markup in prose fields, e.g. `preReqNotes` contains
    `<em> OR </em>`. Left in place it would corrupt display and confuse any
    future prerequisite parser.
    """
    if value is None:
        return None
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def _normalize_supplement(value: str | None) -> str:
    """SOC sends two spaces ('  ') when there is no supplement code.

    Normalized to empty string, never NULL: this field is part of the course
    unique constraint, and in SQL `NULL != NULL`, so a nullable column would
    let Postgres accept unlimited duplicate rows for the same course.
    """
    if value is None:
        return ""
    return value.strip()


def _best_title(raw: RawSocCourse) -> tuple[str, str | None]:
    """Choose the display title, keeping the abbreviated one alongside.

    Measured: `title` is present on 100% of records but abbreviated to ~20
    chars ("COMICS MIDEAST"); `expandedTitle` holds the readable form but is
    present on only 60.8%, and differs from `title` in 2,409 cases.

    So: prefer expandedTitle, fall back to title, and retain both.
    """
    abbrev = (raw.title or "").strip()
    expanded = (raw.expandedTitle or "").strip()

    if expanded:
        return expanded, abbrev or None
    return abbrev, None


class SocNormalizer:
    """Maps SOC course records into CoursePilot's canonical form."""

    def __init__(self, term_code: str) -> None:
        self.term_code = term_code

    def normalize(self, raw: RawSocCourse) -> NormalizedCourse:
        title, title_abbrev = _best_title(raw)

        credits_description = None
        if raw.creditsObject:
            credits_description = raw.creditsObject.get("description")

        school_code = school_description = None
        if raw.school:
            school_code = raw.school.get("code")
            school_description = raw.school.get("description")

        # SOC returns courseDescription empty for 100% of observed records.
        # We map it anyway so a catalog source can fill it later, but we do
        # NOT substitute the title as a stand-in description — that would
        # manufacture content that does not exist upstream.
        description = _clean_text(raw.courseDescription)

        return NormalizedCourse(
            offering_unit_code=raw.offeringUnitCode,
            subject_code=raw.subject,
            course_number=raw.courseNumber,
            supplement_code=_normalize_supplement(raw.supplementCode),
            course_string=raw.courseString,
            title=title,
            title_abbrev=title_abbrev,
            description=description,
            credits=raw.credits,
            credits_description=credits_description,
            level=raw.level,
            school_code=school_code,
            school_description=school_description,
            subject_description=_clean_text(raw.subjectDescription),
            prereq_notes_raw=_clean_text(raw.preReqNotes),
            synopsis_url=(raw.synopsisUrl or "").strip() or None,
            term_code=self.term_code,
            # campusCode is what makes the NB/OB duplicate pair distinct. It
            # lands on the offering, not the course.
            campus_code=(raw.campusCode or "").strip() or "UNKNOWN",
            open_sections_observed=raw.openSections,
            section_count_observed=len(raw.sections),
        )
