"""Command-line entry point for Rutgers course ingestion.

Usage:
    python -m coursepilot_ingestion.cli --limit 25
    python -m coursepilot_ingestion.cli --subject 198 --year 2026 --term 9
    python -m coursepilot_ingestion.cli --show-only

Defaults are deliberately small. Scaling up is an explicit decision, not
something that happens because someone forgot a flag.
"""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.schemas import IngestionStats
from coursepilot_ingestion.section_schemas import SectionIngestionStats
from coursepilot_ingestion.sources.soc import SocQuery

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DEFAULT_CACHE = ROOT / "data" / "raw"

logger = logging.getLogger("coursepilot.ingest")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="coursepilot-ingest",
        description="Ingest Rutgers Schedule of Classes course data into PostgreSQL.",
    )
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--term", default="9", help="9=Fall 1=Spring 7=Summer 0=Winter (only 9 verified)")
    p.add_argument("--campus", default="NB", help="only NB has been verified by this project")
    p.add_argument(
        "--limit",
        type=int,
        default=25,
        help="max courses to LOAD (default 25; the full payload is still archived)",
    )
    p.add_argument("--all", action="store_true", help="load every course (overrides --limit)")
    p.add_argument("--subject", default=None, help="restrict to one subject code, e.g. 198")
    p.add_argument(
        "--stage",
        choices=("courses", "sections", "both"),
        default="courses",
        help=(
            "which pipeline to run. 'sections' requires the matching courses to be "
            "loaded already; 'both' runs courses then sections in that order"
        ),
    )
    p.add_argument(
        "--refetch",
        action="store_true",
        help="bypass the local archive and re-download from Rutgers",
    )
    p.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL_SYNC")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    p.add_argument("--show-only", action="store_true", help="query the database and exit")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def resolve_database_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            "No database URL. Set DATABASE_URL_SYNC or pass --database-url.\n"
            "  Postgres: postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot\n"
            "  (start it with: docker compose up -d db)"
        )
    # The async driver cannot be used with a sync Session; fail clearly rather
    # than with an opaque SQLAlchemy error.
    if "+asyncpg" in url:
        raise SystemExit("Use a sync driver (postgresql+psycopg://), not asyncpg.")
    return url


def show_contents(session: Session) -> None:
    """Query the database back and print what is actually stored.

    This is the proof the pipeline worked - reading from the database rather
    than trusting the pipeline's own report of what it did.
    """
    from app.models import Course, CourseOffering, DataSource, Subject

    print("\n" + "=" * 78)
    print("DATABASE CONTENTS")
    print("=" * 78)

    for label, model in (
        ("data_source", DataSource),
        ("subject", Subject),
        ("course", Course),
        ("course_offering", CourseOffering),
    ):
        count = session.scalar(select(func.count()).select_from(model))
        print(f"  {label:18} {count:>6,} rows")

    source = session.scalar(select(DataSource).order_by(DataSource.retrieved_at.desc()))
    if source:
        print("\nmost recent source")
        print(f"  url           {source.url}")
        print(f"  retrieved_at  {source.retrieved_at}")
        print(f"  term_code     {source.term_code}")
        print(f"  content_hash  {source.content_hash[:16]}...")
        print(f"  archived at   {source.raw_payload_ref}")

    rows = session.scalars(
        select(Course).order_by(Course.course_string, Course.supplement_code).limit(15)
    ).all()
    if not rows:
        print("\n(no courses stored)")
        return

    print(f"\nfirst {len(rows)} courses")
    print(f"  {'course':14} {'sup':4} {'cr':>5} {'lvl':4} {'campuses':10} title")
    print("  " + "-" * 74)
    for c in rows:
        campuses = ",".join(sorted(o.campus_code for o in c.offerings))
        credits = "-" if c.credits is None else f"{c.credits:g}"
        title = c.title[:30]
        print(
            f"  {c.course_string:14} {c.supplement_code or '-':4} {credits:>5} "
            f"{c.level or '-':4} {campuses:10} {title}"
        )

    # Show the two duplicate cases explicitly - they are the interesting part.
    multi = [c for c in session.scalars(select(Course)).all() if len(c.offerings) > 1]
    if multi:
        print("\ncourses offered at multiple campuses (one course, several offerings)")
        for c in multi:
            print(f"  {c.course_string}  ->  {sorted(o.campus_code for o in c.offerings)}")

    prereq = session.scalars(
        select(Course).where(Course.prereq_notes_raw.is_not(None)).limit(2)
    ).all()
    if prereq:
        print("\nprerequisite prose (stored verbatim, NOT parsed)")
        for c in prereq:
            print(f"  {c.course_string}: {c.prereq_notes_raw[:100]}")

    _show_sections(session)


def _show_sections(session: Session) -> None:
    """Print stored section data, read back from the database."""
    from app.models import CourseSection, SectionInstructor, SectionMeeting

    total = session.scalar(select(func.count()).select_from(CourseSection))
    if not total:
        return

    print("\n" + "-" * 78)
    print("SECTIONS")
    print("-" * 78)
    for label, model in (
        ("course_section", CourseSection),
        ("section_meeting", SectionMeeting),
        ("section_instructor", SectionInstructor),
    ):
        print(f"  {label:20} {session.scalar(select(func.count()).select_from(model)):>7,} rows")

    open_n = session.scalar(
        select(func.count()).select_from(CourseSection).where(CourseSection.open_status.is_(True))
    )
    print(f"  {'open sections':20} {open_n:>7,}  (point-in-time; SOC gives no seat counts)")

    rows = session.scalars(
        select(CourseSection).order_by(CourseSection.index_number).limit(8)
    ).all()
    print(f"\n  first {len(rows)} sections")
    print(f"  {'index':7} {'sec':5} {'open':6} {'course':14} meetings")
    print("  " + "-" * 72)
    for s in rows:
        course = s.offering.course
        when = []
        for m in s.meetings:
            when.append(
                "TBA"
                if m.is_tba
                else f"{m.meeting_day}{m.start_time_military}-{m.end_time_military}"
                f"@{m.building_code or '?'}"
            )
        print(
            f"  {s.index_number:7} {s.section_number:5} "
            f"{'OPEN' if s.open_status else 'CLOSED':6} {course.course_string:14} "
            f"{', '.join(when)[:34]}"
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    engine = create_engine(resolve_database_url(args.database_url), future=True)

    with Session(engine) as session:
        if args.show_only:
            show_contents(session)
            return 0

        query = SocQuery(year=args.year, term=args.term, campus=args.campus)
        if not query.is_verified:
            logger.warning(
                "term/campus combination %s has not been verified by this project "
                "(see docs/DATA_SOURCES.md)",
                query,
            )

        cache_dir = pathlib.Path(args.cache_dir)
        limit = None if args.all else args.limit

        if args.stage in ("courses", "both"):
            stats = CourseIngestionPipeline(session, cache_dir).run(
                query,
                limit=limit,
                subject_filter=args.subject,
                use_cache=not args.refetch,
            )
            _print_course_stats(stats)

        if args.stage in ("sections", "both"):
            section_stats = SectionIngestionPipeline(session, cache_dir).run(
                query,
                # Sections are far more numerous than courses (11,992 vs
                # 4,400), so a course-shaped --limit would truncate them
                # oddly. `--all` and `--subject` still apply.
                limit=None if (args.all or args.stage == "both") else limit,
                subject_filter=args.subject,
                use_cache=not args.refetch,
            )
            _print_section_stats(section_stats)

        show_contents(session)

    return 0


def _print_course_stats(stats: IngestionStats) -> None:
    print("\n" + "=" * 78)
    print("COURSE INGESTION RESULT")
    print("=" * 78)
    print(f"  source hash        {stats.source_content_hash[:16]}...")
    print(f"  records in payload {stats.fetched:,}")
    print(f"  parsed             {stats.parsed:,}  (failed: {stats.parse_failed})")
    print(f"  validated          {stats.validated:,}  (failed: {stats.validation_failed})")
    print(f"  courses            +{stats.courses_inserted} new, ~{stats.courses_updated} updated")
    print(f"  offerings          +{stats.offerings_inserted} new, ~{stats.offerings_updated} updated")
    print(f"  subjects           +{stats.subjects_inserted} new")
    if stats.errors:
        print(f"\n  first {min(5, len(stats.errors))} of {len(stats.errors)} error(s):")
        for err in stats.errors[:5]:
            print(f"    - {err}")


def _print_section_stats(stats: SectionIngestionStats) -> None:
    print("\n" + "=" * 78)
    print("SECTION INGESTION RESULT")
    print("=" * 78)
    print(f"  sections in payload {stats.fetched:,}")
    print(f"  parsed              {stats.parsed:,}  (failed: {stats.parse_failed})")
    print(f"  validated           {stats.validated:,}  (failed: {stats.validation_failed})")
    print(f"  sections            +{stats.sections_inserted} new, ~{stats.sections_updated} updated")
    print(f"  meetings written    {stats.meetings_written:,}")
    print(f"  instructors written {stats.instructors_written:,}")
    print(f"  cross-listings      {stats.cross_listings_written:,}")

    # Never buried: an unmatched section is real Rutgers data we did not store.
    print(f"  unmatched offering  {stats.unmatched_offering:,}")
    if stats.unmatched_details:
        print(f"    first {min(3, len(stats.unmatched_details))}:")
        for d in stats.unmatched_details[:3]:
            print(f"    - {d}")
        print("    (expected when courses were ingested with --limit/--subject)")

    if stats.warnings:
        print(f"\n  {len(stats.warnings)} warning(s), first 3:")
        for w in stats.warnings[:3]:
            print(f"    - {w}")
    if stats.errors:
        print(f"\n  first {min(5, len(stats.errors))} of {len(stats.errors)} error(s):")
        for err in stats.errors[:5]:
            print(f"    - {err}")


if __name__ == "__main__":
    sys.exit(main())
