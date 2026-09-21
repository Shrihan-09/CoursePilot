"""End-to-end section pipeline tests.

Exercises fetch -> parse -> normalize -> validate -> load in one pass.

No network: the fetcher is pointed at a temp directory pre-seeded with the
archived fixture, so it takes its cache-hit path.
"""

from __future__ import annotations

import json
import pathlib

from app.models import Course, CourseSection, DataSource, SectionMeeting
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery
from sqlalchemy import func, select

QUERY = SocQuery(year=2026, term="9", campus="NB")


def test_full_section_pipeline(seeded_session, section_cache_dir: pathlib.Path) -> None:
    stats = SectionIngestionPipeline(seeded_session, section_cache_dir).run(QUERY)

    assert stats.parsed == 15
    assert stats.parse_failed == 0
    assert stats.validated == 15
    assert stats.validation_failed == 0
    assert stats.sections_inserted == 15
    assert stats.unmatched_offering == 0
    assert stats.meetings_written == 33
    assert stats.instructors_written == 13

    assert seeded_session.scalar(select(func.count()).select_from(CourseSection)) == 15
    assert seeded_session.scalar(select(func.count()).select_from(SectionMeeting)) == 33


def test_pipeline_is_idempotent(seeded_session, section_cache_dir: pathlib.Path) -> None:
    pipeline = SectionIngestionPipeline(seeded_session, section_cache_dir)

    pipeline.run(QUERY)
    second = pipeline.run(QUERY)

    assert second.sections_inserted == 0
    assert seeded_session.scalar(select(func.count()).select_from(CourseSection)) == 15
    assert seeded_session.scalar(select(func.count()).select_from(SectionMeeting)) == 33


def test_pipeline_reuses_the_course_provenance_row(
    seeded_session, section_cache_dir: pathlib.Path
) -> None:
    # The seeded courses and these sections come from the same payload, but the
    # seed used a different content hash, so a new source row is expected here.
    # What matters is that repeated section runs do not keep adding rows.
    SectionIngestionPipeline(seeded_session, section_cache_dir).run(QUERY)
    before = seeded_session.scalar(select(func.count()).select_from(DataSource))

    SectionIngestionPipeline(seeded_session, section_cache_dir).run(QUERY)
    after = seeded_session.scalar(select(func.count()).select_from(DataSource))

    assert before == after


def test_provenance_is_recorded_on_sections(
    seeded_session, section_cache_dir: pathlib.Path
) -> None:
    stats = SectionIngestionPipeline(seeded_session, section_cache_dir).run(QUERY)

    source = seeded_session.scalar(
        select(DataSource).where(DataSource.content_hash == stats.source_content_hash)
    )
    assert source is not None
    assert source.term_code == "20269"

    for section in seeded_session.scalars(select(CourseSection)).all():
        assert section.source_id == source.id


def test_subject_filter(seeded_session, section_cache_dir: pathlib.Path) -> None:
    stats = SectionIngestionPipeline(seeded_session, section_cache_dir).run(
        QUERY, subject_filter="750"
    )

    assert stats.validated == 6  # 3 lecture + 3 lab sections
    rows = seeded_session.scalars(select(CourseSection)).all()
    assert {r.offering.course.subject_code for r in rows} == {"750"}


def test_unmatched_sections_reported_when_courses_absent(
    session, section_cache_dir: pathlib.Path
) -> None:
    # `session` (not `seeded_session`) has NO courses loaded, so every section
    # is unmatched. None may be inserted, and none may be invented.
    stats = SectionIngestionPipeline(session, section_cache_dir).run(QUERY)

    assert stats.validated == 15
    assert stats.unmatched_offering == 15
    assert stats.sections_inserted == 0
    assert session.scalar(select(func.count()).select_from(CourseSection)) == 0
    assert session.scalar(select(func.count()).select_from(Course)) == 0
    assert len(stats.unmatched_details) > 0


def test_bad_section_does_not_abort_the_batch(
    seeded_session, tmp_path: pathlib.Path, section_payload: list[dict]
) -> None:
    payload = json.loads(json.dumps(section_payload))
    del payload[0]["sections"][0]["index"]
    (tmp_path / "soc_courses_2026_9_NB.json").write_text(json.dumps(payload), encoding="utf-8")

    stats = SectionIngestionPipeline(seeded_session, tmp_path).run(QUERY)

    assert stats.parse_failed == 1
    assert stats.parsed == 14
    assert stats.sections_inserted == 14


def test_warnings_are_surfaced(seeded_session, section_cache_dir: pathlib.Path) -> None:
    # The fixture contains the real end <= start meeting; it must be reported
    # rather than silently accepted or silently dropped.
    stats = SectionIngestionPipeline(seeded_session, section_cache_dir).run(QUERY)

    assert any("<= start" in w for w in stats.warnings)


def test_rollback_when_commit_disabled(
    seeded_session, section_cache_dir: pathlib.Path
) -> None:
    SectionIngestionPipeline(seeded_session, section_cache_dir).run(QUERY, commit=False)

    assert seeded_session.scalar(select(func.count()).select_from(CourseSection)) == 0
