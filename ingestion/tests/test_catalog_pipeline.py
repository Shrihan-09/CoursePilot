"""Catalog course-description ingestion (Phase 3.5, Objective A).

Runs against the REAL archived Rutgers catalog pages for both catalog years.
No network: the fetcher is pointed at a temp directory pre-seeded with the
archive, which also exercises the archive path itself.
"""

from __future__ import annotations

import pathlib
import shutil
from decimal import Decimal

import pytest
from app.models import CatalogCourseEntry, Course, DataSource
from coursepilot_ingestion.catalog_schemas import parse_credits
from coursepilot_ingestion.normalizers.catalog import CatalogNormalizer
from coursepilot_ingestion.parsers.catalog import CatalogParser
from coursepilot_ingestion.pipelines.catalog import CatalogIngestionPipeline
from coursepilot_ingestion.sources.catalog import CatalogQuery
from coursepilot_ingestion.validators.catalog import CatalogValidator
from sqlalchemy import func, select

ARCHIVE = pathlib.Path("data/raw/catalog")
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

Y26 = "2026-2027"
Y25 = "2025-2026"


@pytest.fixture
def catalog_cache(tmp_path: pathlib.Path) -> pathlib.Path:
    """A fetcher cache seeded with both archived catalog years."""
    for year, name in ((Y26, "cs_26-27.html"), (Y25, "cs_25-26.html")):
        src = REPO_ROOT / ARCHIVE / name
        if not src.exists():
            pytest.skip(f"catalog archive missing: {src}")
        shutil.copyfile(src, tmp_path / CatalogQuery(catalog_year=year).archive_name)
    return tmp_path


@pytest.fixture
def catalog_html() -> str:
    src = REPO_ROOT / ARCHIVE / "cs_26-27.html"
    if not src.exists():
        pytest.skip(f"catalog archive missing: {src}")
    return src.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def test_parses_real_catalog_page(catalog_html: str) -> None:
    result = CatalogParser().parse(catalog_html)

    assert len(result.courses) == 44
    assert result.failures == []
    assert all(c.course_string.startswith("01:") for c in result.courses)


def test_parser_extracts_descriptions(catalog_html: str) -> None:
    """The whole reason the catalog matters: SOC has zero descriptions."""
    result = CatalogParser().parse(catalog_html)
    with_desc = [c for c in result.courses if len(c.description) > 30]

    assert len(with_desc) == 44


def test_split_title_does_not_swallow_the_next_course(catalog_html: str) -> None:
    """Regression for a parser bug that silently lost a REQUIRED course.

    01:198:110's title contains an internal `</em></strong><strong><em>`
    split. With a single whole-entry regex, its title group ran past the end
    of its own entry and consumed 01:198:111 entirely - so the course the CS
    major actually requires vanished, with no parse failure reported.

    Splitting on the course-code marker first makes this impossible: a
    malformed chunk can only lose itself, never its neighbour.
    """
    result = CatalogParser().parse(catalog_html)
    codes = [c.course_string for c in result.courses]

    assert "01:198:110" in codes
    assert "01:198:111" in codes, "the split-title bug dropped this required course"

    entry = next(c for c in result.courses if c.course_string == "01:198:111")
    assert entry.title == "Introduction to Computer Science"
    assert entry.credits_raw == "4"
    assert "Intensive introduction" in entry.description


def test_course_with_no_published_credits_keeps_title_and_description(
    catalog_html: str,
) -> None:
    """01:198:110 publishes a title and description but NO "(N)" credits.

    Title and credits are parsed independently for exactly this reason -
    requiring the parentheses would discard all three fields.
    """
    result = CatalogParser().parse(catalog_html)
    entry = next(c for c in result.courses if c.course_string == "01:198:110")

    assert entry.title == "Principles to Computer Science"
    assert entry.credits_raw == ""
    assert len(entry.description) > 50

    normalized = CatalogNormalizer(Y26).normalize(entry)
    assert normalized.credits_min is None
    assert normalized.credits_max is None
    assert normalized.description is not None


def test_every_parsed_entry_has_a_title(catalog_html: str) -> None:
    """A titleless entry means the chunk parser lost its bearings."""
    result = CatalogParser().parse(catalog_html)
    untitled = [c.course_string for c in result.courses if not c.title.strip()]
    assert untitled == []


def test_parser_retains_requirement_prose(catalog_html: str) -> None:
    """Prose blocks are kept so curated requirements can be re-checked."""
    result = CatalogParser().parse(catalog_html)
    joined = " ".join(result.prose_blocks)

    assert "Major Requirements" in joined
    assert "six required courses" in joined


def test_parser_rejects_non_catalog_html() -> None:
    with pytest.raises(ValueError, match="no __NUXT_DATA__"):
        CatalogParser().parse("<html><body>not a catalog page</body></html>")


def test_parser_rejects_malformed_payload() -> None:
    bad = '<script id="__NUXT_DATA__">{not json}</script>'
    with pytest.raises(ValueError, match="not valid JSON"):
        CatalogParser().parse(bad)


def test_parser_rejects_non_array_payload() -> None:
    bad = '<script id="__NUXT_DATA__">{"a": 1}</script>'
    with pytest.raises(ValueError, match="array"):
        CatalogParser().parse(bad)


# --------------------------------------------------------------------------
# credits, including the range case
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3", (Decimal(3), Decimal(3))),
        ("1", (Decimal(1), Decimal(1))),
        ("3-4", (Decimal(3), Decimal(4))),
        ("", (None, None)),
        (None, (None, None)),
        ("BA", (None, None)),          # unparseable -> no guess
        ("3 or 4", (None, None)),      # not a form the catalog uses
    ],
)
def test_parse_credits(raw, expected) -> None:
    assert parse_credits(raw) == expected


def test_single_credit_yields_equal_min_and_max(catalog_html: str) -> None:
    parsed = CatalogParser().parse(catalog_html)
    entries = [CatalogNormalizer(Y26).normalize(r) for r in parsed.courses]
    singles = [e for e in entries if e.credits_raw == "4"]

    assert singles
    for e in singles:
        assert e.credits_min == e.credits_max == Decimal(4)
        assert e.is_credit_range is False


def test_real_credit_range_is_preserved(catalog_html: str) -> None:
    """01:198:442 is published as "3-4" - SOC cannot express this."""
    parsed = CatalogParser().parse(catalog_html)
    entries = {e.course_string: e for e in (CatalogNormalizer(Y26).normalize(r) for r in parsed.courses)}

    entry = entries["01:198:442"]
    assert entry.credits_raw == "3-4"
    assert entry.credits_min == Decimal(3)
    assert entry.credits_max == Decimal(4)
    assert entry.is_credit_range is True


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def test_real_catalog_passes_validation(catalog_html: str) -> None:
    parsed = CatalogParser().parse(catalog_html)
    entries = [CatalogNormalizer(Y26).normalize(r) for r in parsed.courses]

    outcome = CatalogValidator().validate(entries)
    assert outcome.rejected == [], outcome.error_messages
    assert len(outcome.valid) == 44


def test_entry_with_no_title_or_description_is_rejected() -> None:
    from coursepilot_ingestion.catalog_schemas import NormalizedCatalogCourse

    empty = NormalizedCatalogCourse(course_string="01:198:111", catalog_year=Y26)
    outcome = CatalogValidator().validate([empty])

    assert outcome.valid == []
    assert "nothing to store" in outcome.error_messages[0]


def test_missing_description_warns_but_does_not_reject() -> None:
    from coursepilot_ingestion.catalog_schemas import NormalizedCatalogCourse

    entry = NormalizedCatalogCourse(
        course_string="01:198:111", catalog_year=Y26, title="Some Course"
    )
    outcome = CatalogValidator().validate([entry])

    assert len(outcome.valid) == 1
    assert any(w.field_name == "description" for w in outcome.warnings)


def test_malformed_course_string_is_rejected() -> None:
    from coursepilot_ingestion.catalog_schemas import NormalizedCatalogCourse
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="Rutgers format"):
        NormalizedCatalogCourse(course_string="CS-111", catalog_year=Y26, title="x")


def test_inverted_credit_range_is_rejected() -> None:
    from coursepilot_ingestion.catalog_schemas import NormalizedCatalogCourse
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="inverted"):
        NormalizedCatalogCourse(
            course_string="01:198:111",
            catalog_year=Y26,
            title="x",
            credits_min=Decimal(4),
            credits_max=Decimal(3),
        )


# --------------------------------------------------------------------------
# full pipeline
# --------------------------------------------------------------------------


def test_full_catalog_pipeline(cs_session, catalog_cache: pathlib.Path) -> None:
    stats = CatalogIngestionPipeline(cs_session, catalog_cache).run(
        CatalogQuery(catalog_year=Y26)
    )

    assert stats.parsed == 44
    assert stats.validated == 44
    assert stats.validation_failed == 0
    assert stats.entries_inserted == 44
    assert stats.descriptions_populated == 44
    assert stats.credit_ranges == 1
    assert cs_session.scalar(select(func.count()).select_from(CatalogCourseEntry)) == 44


def test_entries_map_to_existing_courses(cs_session, catalog_cache: pathlib.Path) -> None:
    """Course identity is reused - no Course is ever created here."""
    before = cs_session.scalar(select(func.count()).select_from(Course))
    CatalogIngestionPipeline(cs_session, catalog_cache).run(CatalogQuery(catalog_year=Y26))
    after = cs_session.scalar(select(func.count()).select_from(Course))

    assert before == after, "catalog ingestion must not create Course rows"

    linked = cs_session.scalars(
        select(CatalogCourseEntry).where(CatalogCourseEntry.course_id.is_not(None))
    ).all()
    assert linked, "some entries should link to the fixture's courses"
    for entry in linked:
        course = cs_session.get(Course, entry.course_id)
        assert course.course_string == entry.course_string


def test_unmapped_entries_are_stored_not_dropped(
    cs_session, catalog_cache: pathlib.Path
) -> None:
    """The catalog is a superset of what SOC offers, so an entry with no
    Course row is normal. It is kept, unlinked, and reported."""
    stats = CatalogIngestionPipeline(cs_session, catalog_cache).run(
        CatalogQuery(catalog_year=Y26)
    )

    assert stats.unmapped, "the CS fixture does not contain all 43 catalog courses"
    unlinked = cs_session.scalars(
        select(CatalogCourseEntry).where(CatalogCourseEntry.course_id.is_(None))
    ).all()
    assert {e.course_string for e in unlinked} == set(stats.unmapped)
    # And they still carry their descriptions.
    assert all(e.description for e in unlinked)


def test_soc_credits_are_not_overwritten(cs_session, catalog_cache: pathlib.Path) -> None:
    """SOC credits and catalog credits are different facts."""
    course = cs_session.scalar(
        select(Course).where(Course.course_string == "01:198:111", Course.supplement_code == "")
    )
    soc_credits = course.credits

    CatalogIngestionPipeline(cs_session, catalog_cache).run(CatalogQuery(catalog_year=Y26))
    cs_session.refresh(course)

    assert course.credits == soc_credits


def test_catalog_pipeline_is_idempotent(cs_session, catalog_cache: pathlib.Path) -> None:
    pipeline = CatalogIngestionPipeline(cs_session, catalog_cache)
    pipeline.run(CatalogQuery(catalog_year=Y26))
    second = pipeline.run(CatalogQuery(catalog_year=Y26))

    assert second.entries_inserted == 0
    assert cs_session.scalar(select(func.count()).select_from(CatalogCourseEntry)) == 44
    # Unchanged page -> one provenance row for that page, not two.
    assert (
        cs_session.scalar(
            select(func.count())
            .select_from(DataSource)
            .where(DataSource.content_hash == second.source_content_hash)
        )
        == 1
    )


def test_catalog_years_stay_isolated(cs_session, catalog_cache: pathlib.Path) -> None:
    """Identical content does not mean identical version identity."""
    pipeline = CatalogIngestionPipeline(cs_session, catalog_cache)
    pipeline.run(CatalogQuery(catalog_year=Y26))
    pipeline.run(CatalogQuery(catalog_year=Y25))

    per_year = dict(
        cs_session.execute(
            select(CatalogCourseEntry.catalog_year, func.count()).group_by(
                CatalogCourseEntry.catalog_year
            )
        ).all()
    )
    assert per_year == {Y26: 44, Y25: 44}

    # The same course has a separate row per year.
    rows = cs_session.scalars(
        select(CatalogCourseEntry).where(CatalogCourseEntry.course_string == "01:198:111")
    ).all()
    assert {r.catalog_year for r in rows} == {Y26, Y25}


def test_provenance_is_recorded(cs_session, catalog_cache: pathlib.Path) -> None:
    stats = CatalogIngestionPipeline(cs_session, catalog_cache).run(
        CatalogQuery(catalog_year=Y26)
    )

    # The curated-requirements fixture also carries kind
    # "rutgers_official_catalog" (it IS from the catalog), so filter by the
    # page hash rather than by kind.
    source = cs_session.scalar(
        select(DataSource).where(DataSource.content_hash == stats.source_content_hash)
    )
    assert source is not None
    assert source.academic_year == Y26
    assert source.url.startswith("https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu")
    assert source.retrieved_at is not None
    assert source.raw_payload_ref is not None

    for entry in cs_session.scalars(select(CatalogCourseEntry)).all():
        assert entry.source_id == source.id
        assert entry.source_url
        assert entry.catalog_year == Y26


def test_descriptions_are_queryable(cs_session, catalog_cache: pathlib.Path) -> None:
    """The Phase 1 gap, closed."""
    CatalogIngestionPipeline(cs_session, catalog_cache).run(CatalogQuery(catalog_year=Y26))

    entry = cs_session.scalar(
        select(CatalogCourseEntry).where(
            CatalogCourseEntry.course_string == "01:198:111",
            CatalogCourseEntry.catalog_year == Y26,
        )
    )
    assert entry.description and len(entry.description) > 50
    assert entry.title


def test_unknown_catalog_year_is_refused() -> None:
    """Subdomains are inconsistent between years, so a year we have not
    verified must raise rather than construct a guessed URL."""
    with pytest.raises(ValueError, match="must be discovered"):
        CatalogQuery(catalog_year="2099-2100").url
