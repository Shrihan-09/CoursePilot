"""Read-only lifecycle coverage. Source pages and credential candidates are not support."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CatalogPage, ProgramCandidate, ProgramReview, ProgramVersion


def coverage_report(session: Session) -> dict:
    years: dict[str, dict] = {}

    def bucket(year):
        return years.setdefault(
            year,
            {
                "discovered_catalog_pages": 0,
                "fetched_catalog_pages": 0,
                "parsed_credential_candidates": 0,
                "encoded_program_versions": 0,
                "currently_validated_programs": 0,
                "currently_human_reviewed_programs": 0,
                "published_programs": 0,
                "legacy_published_programs": 0,
                "lifecycle_states": {},
            },
        )

    for page in session.scalars(select(CatalogPage)):
        b = bucket(page.catalog_year)
        b["discovered_catalog_pages"] += 1
        b["fetched_catalog_pages"] += bool(page.snapshot_id)
    for year in session.scalars(
        select(CatalogPage.catalog_year).join(
            ProgramCandidate, ProgramCandidate.catalog_page_id == CatalogPage.id
        )
    ):
        bucket(year)["parsed_credential_candidates"] += 1
    reviews = {}
    for review in session.scalars(
        select(ProgramReview).order_by(
            ProgramReview.reviewed_at, ProgramReview.created_at, ProgramReview.id
        )
    ):
        reviews[review.program_version_id] = review
    for version in session.scalars(select(ProgramVersion)):
        b = bucket(version.catalog_year)
        b["encoded_program_versions"] += 1
        state = version.lifecycle_state
        b["lifecycle_states"][state] = b["lifecycle_states"].get(state, 0) + 1
        b["currently_validated_programs"] += bool(
            version.validated_at and state in {"validated", "reviewed", "published"}
        )
        review = reviews.get(version.id)
        b["currently_human_reviewed_programs"] += bool(
            review
            and review.decision == "approved"
            and state in {"reviewed", "published"}
            and review.definition_sha256 == version.definition_sha256
            and review.source_prose_sha256 == version.source_prose_sha256
        )
        b["published_programs"] += state == "published"
        b["legacy_published_programs"] += (
            state == "published" and version.publication_basis == "legacy_curated"
        )
    return {
        "schema_version": "1.0.0",
        "catalog_years": dict(sorted(years.items())),
        "scope_note": ("Pages and parsed candidates do not establish "
                       "evaluable or published support."),
    }
