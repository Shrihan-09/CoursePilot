"""Descriptor for the Rutgers Schedule of Classes JSON source.

Keeping URL construction and source metadata in one place means the fetcher
does not hard-code URLs and the pipeline does not build query strings.

See docs/DATA_SOURCES.md for what was actually verified about this endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass

# sis.rutgers.edu 301-redirects here; we use the final host directly.
SOC_BASE = "https://classes.rutgers.edu/soc/api"

# Term digits, verified against real responses - see docs/DATA_SOURCES.md §1c.
# Originally community-reported; all four confirmed in Phase 2.5.
TERM_FALL = "9"
TERM_SPRING = "1"
TERM_SUMMER = "7"
TERM_WINTER = "0"

# All four term digits verified against real 200 responses in Phase 2.5
# (scripts/probe_term_availability.py, 2026-09-12):
#   Fall 2025 / Spring 2026 / Summer 2026 / Fall 2026 / Winter 2027 all
#   returned populated payloads. Fall 2026 and Winter 2027 have additionally
#   been ingested end-to-end.
VERIFIED_TERMS = {TERM_FALL, TERM_SPRING, TERM_SUMMER, TERM_WINTER}
# Still only NB has ever been REQUESTED; OB appears inside NB payloads but has
# not been fetched as its own campus parameter.
VERIFIED_CAMPUSES = {"NB"}


@dataclass(frozen=True, slots=True)
class SocQuery:
    """Identifies one SOC request."""

    year: int
    term: str
    campus: str

    @property
    def term_code(self) -> str:
        """CoursePilot's term code, e.g. "20269".

        Matches the `effective` field SOC itself emits inside `coreCodes`, so
        we adopt the source's convention rather than inventing a parallel one.
        """
        return f"{self.year}{self.term}"

    @property
    def courses_url(self) -> str:
        return f"{SOC_BASE}/courses.json?year={self.year}&term={self.term}&campus={self.campus}"

    @property
    def is_verified(self) -> bool:
        """Whether this exact parameter combination has been probed.

        The pipeline warns on unverified combinations rather than refusing
        them — but it says so, so a surprising result is traceable.
        """
        return self.term in VERIFIED_TERMS and self.campus in VERIFIED_CAMPUSES

    def __str__(self) -> str:
        return f"SOC(year={self.year}, term={self.term}, campus={self.campus})"


SOURCE_KIND = "rutgers_official_api"
USER_AGENT = "CoursePilot/0.1 (Rutgers student academic planning project)"
