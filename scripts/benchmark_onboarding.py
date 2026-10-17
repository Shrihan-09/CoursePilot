"""Phase 7.0 onboarding timings (DATA_MODEL section 46.13). SYNTHETIC data only.

Run from backend/ against a *_test database:

    DATABASE_URL_SYNC=postgresql+psycopg://.../coursepilot_test         python ../scripts/benchmark_onboarding.py

Measures the isolated (child-process) extractor against in-process pypdf,
and staging + confirmation of a 60-row synthetic transcript.
"""
import os
import statistics
import sys
import time
from datetime import UTC, datetime

sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), "tests", "support"))
from onboarding_world import build, new_account  # noqa: E402
from synthetic_transcript import build_pdf  # noqa: E402

from app.services import onboarding  # noqa: E402
from app.services.transcripts import imports  # noqa: E402
from app.services.transcripts.extract import isolated_pdf_extractor  # noqa: E402
from app.services.transcripts.storage import MemoryTranscriptStorage  # noqa: E402
from tests.test_multi_program import _session  # noqa: E402
from tests.test_transcript_import_api import in_process  # noqa: E402


def lines(world, n):
    out = ["SYNTHETIC"]
    codes = list(world["code"].values())[:5]
    for t in range(n // 5):
        out.append(f"FALL {2000 + t}")
        out += [f"{c} SYN TITLE 3.0 B" for c in codes]
    out.append("CUMULATIVE GPA 3.000")
    return out


def main():
    with _session() as s:
        world = build(s)
        account = new_account(s)
        onboarding.create_profile(s, account, program_key=world["keys"]["pub"],
                                  catalog_year=world["year"])
        s.commit()

    pdf = build_pdf([lines(world, 60)[i:i + 40] for i in range(0, 62, 40)])
    iso = isolated_pdf_extractor(max_pages=20, max_chars=200_000, timeout_seconds=60)
    spawn = []
    for _ in range(5):
        t = time.perf_counter()
        iso(pdf)
        spawn.append((time.perf_counter() - t) * 1000)
    inproc = []
    for _ in range(5):
        t = time.perf_counter()
        in_process(pdf)
        inproc.append((time.perf_counter() - t) * 1000)

    with _session() as s:
        t = time.perf_counter()
        imp, _ = imports.upload_transcript(s, account, pdf, storage=MemoryTranscriptStorage(),
                                           extractor=in_process, now=datetime.now(UTC),
                                           max_bytes=10**7)
        stage_ms = (time.perf_counter() - t) * 1000
        rows, version, iid = len(imp.rows), imp.version, imp.id
    with _session() as s:
        t = time.perf_counter()
        imports.confirm_import(s, account, iid, expected_version=version, now=datetime.now(UTC))
        s.commit()
        confirm_ms = (time.perf_counter() - t) * 1000
    print(f"pdf bytes={len(pdf)} rows={rows}")
    print(f"isolated extraction (spawn+pypdf) median {statistics.median(spawn):.0f} ms "
          f"(min {min(spawn):.0f}, max {max(spawn):.0f}); in-process pypdf median "
          f"{statistics.median(inproc):.1f} ms")
    print(f"stage {rows} rows (parse+normalize+2 commits) {stage_ms:.0f} ms; "
          f"confirm {rows} rows {confirm_ms:.0f} ms")


if __name__ == "__main__":
    main()
