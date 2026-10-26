"""Backfill term-scoped SOC Core codes (`offering_core_code`, Phase 7.6) from the archives.

The course pipeline loads a term's coreCodes from now on. This script reads
them for terms loaded BEFORE that, from the archived SOC payloads. A term is
read only when its archive is EXACTLY a payload CoursePilot already loaded
(same content hash as a `data_source` row); otherwise it is skipped and said
so. It never creates a provenance row, a course or an offering.

Idempotent: a second run reports every row unchanged.

Usage:  DATABASE_URL_SYNC=... python scripts/backfill_core_codes.py [archive_dir]
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import sys
from datetime import UTC, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "ingestion")]

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import DataSource  # noqa: E402
from coursepilot_ingestion.loaders.core_codes import CoreCodeLoader  # noqa: E402
from coursepilot_ingestion.normalizers.soc import SocNormalizer  # noqa: E402
from coursepilot_ingestion.parsers.soc import SocParser  # noqa: E402
from coursepilot_ingestion.schemas import IngestionStats  # noqa: E402
from coursepilot_ingestion.sources.soc import SOURCE_KIND, SocQuery  # noqa: E402
from coursepilot_ingestion.validators.course import CourseValidator  # noqa: E402

ARCHIVE = re.compile(r"^soc_courses_(\d{4})_(\d)_NB\.json$")
KEEP = ("inserted", "updated", "unchanged", "deleted", "skipped_malformed")


def main() -> None:
    folder = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "raw"
    url = os.environ.get("DATABASE_URL_SYNC",
                         "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot")
    with Session(create_engine(url)) as s:
        for path in sorted(folder.iterdir()):
            m = ARCHIVE.match(path.name)
            if not m:
                continue
            query = SocQuery(year=int(m.group(1)), term=m.group(2), campus="NB")
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            source = s.scalars(select(DataSource).where(
                DataSource.kind == SOURCE_KIND, DataSource.term_code == query.term_code,
                DataSource.content_hash == digest)
                .order_by(DataSource.retrieved_at.desc())).first()
            if source is None:
                print(json.dumps({"term": query.term_code,
                                  "skipped": "archive_not_a_loaded_payload"}))
                continue
            parsed = SocParser().parse(content)
            normalizer = SocNormalizer(term_code=query.term_code)
            normalized = []
            for raw in parsed.courses:
                try:
                    normalized.append(normalizer.normalize(raw))
                except Exception:  # noqa: BLE001 - the pipeline skips these too
                    continue
            valid = CourseValidator().validate(normalized).valid
            stats = IngestionStats(started_at=datetime.now(UTC))
            CoreCodeLoader(s).load(parsed.courses, valid, source, stats)
            s.commit()
            print(json.dumps({"term": query.term_code,
                              **{k: stats.core_codes.get(k, 0) for k in KEEP}}), flush=True)


if __name__ == "__main__":
    main()
