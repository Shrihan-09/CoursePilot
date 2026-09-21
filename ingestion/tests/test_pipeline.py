"""End-to-end pipeline tests.

Exercises fetch -> parse -> normalize -> validate -> load in one pass.

No network: the fetcher is pointed at a temp directory pre-seeded with the
archived fixture, so it takes its cache-hit path. That also verifies the
archive path itself works, which a mocked fetcher would not.
"""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest
from app.models import Course, CourseOffering, DataSource
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery
from sqlalchemy import func, select

QUERY = SocQuery(year=2026, term="9", campus="NB")
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "soc_courses_sample.json"


@pytest.fixture
def cache_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A cache directory seeded with the fixture under the archive name the
    fetcher derives from the query."""
    shutil.copyfile(FIXTURE, tmp_path / "soc_courses_2026_9_NB.json")
    return tmp_path


def test_full_pipeline(session, cache_dir: pathlib.Path) -> None:
    stats = CourseIngestionPipeline(session, cache_dir).run(QUERY)

    assert stats.fetched == 10
    assert stats.parsed == 10
    assert stats.parse_failed == 0
    assert stats.validated == 10
    assert stats.validation_failed == 0
    assert stats.courses_inserted == 9   # NB/OB pair collapses to one course
    assert stats.offerings_inserted == 10

    assert session.scalar(select(func.count()).select_from(Course)) == 9
    assert session.scalar(select(func.count()).select_from(CourseOffering)) == 10


def test_pipeline_is_idempotent(session, cache_dir: pathlib.Path) -> None:
    pipeline = CourseIngestionPipeline(session, cache_dir)

    pipeline.run(QUERY)
    second = pipeline.run(QUERY)

    assert second.courses_inserted == 0
    assert second.offerings_inserted == 0
    assert session.scalar(select(func.count()).select_from(Course)) == 9
    # Byte-identical payload, so provenance must not churn.
    assert session.scalar(select(func.count()).select_from(DataSource)) == 1


def test_limit_restricts_what_is_loaded(session, cache_dir: pathlib.Path) -> None:
    stats = CourseIngestionPipeline(session, cache_dir).run(QUERY, limit=3)

    assert stats.fetched == 10          # the whole payload is still parsed
    assert stats.validated == 3         # but only 3 are loaded
    assert session.scalar(select(func.count()).select_from(Course)) <= 3


def test_subject_filter(session, cache_dir: pathlib.Path) -> None:
    stats = CourseIngestionPipeline(session, cache_dir).run(QUERY, subject_filter="013")

    assert stats.validated > 0
    rows = session.scalars(select(Course)).all()
    assert {c.subject_code for c in rows} == {"013"}


def test_provenance_is_recorded(session, cache_dir: pathlib.Path) -> None:
    stats = CourseIngestionPipeline(session, cache_dir).run(QUERY)

    source = session.scalar(select(DataSource))
    assert source is not None
    assert source.content_hash == stats.source_content_hash
    assert source.term_code == "20269"
    assert source.academic_year == "2026"
    assert source.url.startswith("https://classes.rutgers.edu/soc/api/courses.json")
    assert source.raw_payload_ref is not None
    assert source.record_count == 10

    # Every course must be traceable back to that source.
    for course in session.scalars(select(Course)).all():
        assert course.source_id == source.id


def test_bad_record_does_not_abort_the_batch(session, tmp_path: pathlib.Path) -> None:
    records = json.loads(FIXTURE.read_text(encoding="utf-8"))
    broken = dict(records[0])
    del broken["courseString"]
    (tmp_path / "soc_courses_2026_9_NB.json").write_text(
        json.dumps([*records, broken]), encoding="utf-8"
    )

    stats = CourseIngestionPipeline(session, tmp_path).run(QUERY)

    assert stats.parse_failed == 1
    assert stats.parsed == 10
    assert stats.courses_inserted == 9   # the good records still land
    assert any("courseString" in e for e in stats.errors)


def test_rollback_when_commit_disabled(session, cache_dir: pathlib.Path) -> None:
    CourseIngestionPipeline(session, cache_dir).run(QUERY, commit=False)

    assert session.scalar(select(func.count()).select_from(Course)) == 0
