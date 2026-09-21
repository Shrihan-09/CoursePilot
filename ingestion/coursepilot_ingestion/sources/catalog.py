"""Descriptor for the Rutgers (Coursedog) catalog source.

Investigated in Phase 3; see docs/DATA_SOURCES.md §2 for the measurements.

Two facts drive this module:

1. **Catalog year is encoded in the subdomain**, so a catalog year maps to a
   whole different host rather than a query parameter.

2. **The subdomain naming is inconsistent between years**
   (`newbrunswick-26-27-undergrad` vs `newbrunswick-25-26-undergrad-archive`),
   so URLs CANNOT be generated from a year. They are listed explicitly here
   and must be discovered from main.catalogs.rutgers.edu when a new year
   appears - guessing a pattern would silently 404 or, worse, fetch the wrong
   year.

The public Coursedog API (app.coursedog.com/api/v1/...) returns HTTP 401, so
the rendered page's embedded `__NUXT_DATA__` payload is the access path. No
browser engine is required.
"""

from __future__ import annotations

from dataclasses import dataclass

USER_AGENT = "CoursePilot/0.1 (Rutgers student academic planning project)"
SOURCE_KIND = "rutgers_official_catalog"

# Verified HTTP 200 on 2026-09-13. Do not derive these from a year.
CATALOG_HOSTS: dict[str, str] = {
    "2026-2027": "https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu",
    "2025-2026": "https://newbrunswick-25-26-undergrad-archive.catalogs.rutgers.edu",
}

# Program pages, by the catalog's own path. Only CS is used in this phase;
# the structure generalises but scaling is deliberately out of scope.
PROGRAM_PATHS: dict[str, str] = {
    "computer-science-198": "/schools/sas/program-listing/computer-science-198",
}


@dataclass(frozen=True, slots=True)
class CatalogQuery:
    """Identifies one catalog program page in one catalog year."""

    catalog_year: str
    program_key: str = "computer-science-198"

    @property
    def is_known(self) -> bool:
        return self.catalog_year in CATALOG_HOSTS and self.program_key in PROGRAM_PATHS

    @property
    def url(self) -> str:
        if not self.is_known:
            raise ValueError(
                f"unknown catalog year {self.catalog_year!r} or program "
                f"{self.program_key!r}. Catalog subdomains are inconsistent between "
                "years and must be discovered from main.catalogs.rutgers.edu, not guessed."
            )
        return CATALOG_HOSTS[self.catalog_year] + PROGRAM_PATHS[self.program_key]

    @property
    def archive_name(self) -> str:
        year = self.catalog_year.replace("-", "_")
        return f"catalog_{self.program_key}_{year}.html"

    def __str__(self) -> str:
        return f"Catalog({self.catalog_year}, {self.program_key})"
