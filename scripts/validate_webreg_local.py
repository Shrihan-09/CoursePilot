"""Local compatibility check of a REAL WebReg "View / Print Schedule" PDF.

Runs the production path - the size check, the isolated killable layout
extractor, the positioned parser - and prints STRUCTURE ONLY: the outcome,
a closed error code, page and row counts. It never prints, logs or writes a
course, section, index number, term, meeting or any other document text.

The PDF must live OUTSIDE the repository; the script refuses a path inside it.

Optional --match counts match states (EXACT_MATCH / NO_MATCH / ...) against the
SOC sections in DATABASE_URL_SYNC, inside a transaction that is rolled back.
That database must be at migration 722schedule01 and hold SOC data for the
schedule's term; otherwise every row honestly reports NO_MATCH.

Usage:  python scripts/validate_webreg_local.py <path-to-pdf> [--match]
"""

from __future__ import annotations

import collections
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    path = pathlib.Path(args[0]).resolve()
    if path.is_relative_to(ROOT):
        print("REFUSED: keep the real PDF outside the repository")
        return 2
    if not path.is_file():
        print("REFUSED: file not found")
        return 2

    from app.core.config import get_settings
    from app.domain.webreg import PARSER_VERSION, ScheduleParseError, parse_schedule
    from app.services.scheduling.pdf_worker import default_schedule_extractor
    from app.services.transcripts.extract import ExtractionFailed, check_document

    data = path.read_bytes()
    print(f"parser_version: {PARSER_VERSION}")
    try:
        check_document(data, get_settings().transcript_max_bytes)
        extracted = default_schedule_extractor()(data)
        rows = parse_schedule(extracted.pages, extracted.layout)
    except (ScheduleParseError, ExtractionFailed) as exc:
        # Closed codes only (UNSUPPORTED_LAYOUT, SCHEDULE_TERM_AMBIGUOUS, ...).
        print(f"result: REFUSED {exc.args[0]}")
        return 1
    print("result: PARSED")
    print(f"pages: {len(extracted.pages)}")
    print(f"rows: {len(rows)}")
    print(f"rows_with_index: {sum(r['index_number'] is not None for r in rows)}")
    print(
        f"rows_without_index (arrangement fallback): {sum(r['index_number'] is None for r in rows)}"
    )
    print(f"distinct_terms: {len({r['term_code'] for r in rows})}")

    if "--match" in sys.argv:
        import uuid

        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from app.services.scheduling.imports import resolve_rows

        engine = create_engine(get_settings().database_url_sync)
        with Session(engine) as session:
            try:
                resolved = resolve_rows(session, uuid.uuid4(), rows)
                counts = collections.Counter(r["match_status"] for r in resolved)
                for status in ("EXACT_MATCH", "MULTIPLE_MATCHES", "NO_MATCH", "NEEDS_CONFIRMATION"):
                    print(f"{status}: {counts.get(status, 0)}")
            finally:
                session.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
