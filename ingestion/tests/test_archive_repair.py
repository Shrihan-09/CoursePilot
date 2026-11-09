"""Availability rows never point at a missing archive (Prompt 11 Part B, item 1).

Files are written under pytest's tmp_path (system temp), never under data/raw.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.models.availability import AvailabilityRefresh
from coursepilot_ingestion.archive_repair import repair_missing_archives


def _row(session, path, body: bytes | None, *, sha: str | None = None):
    row = AvailabilityRefresh(
        term_code="20270", source_url="https://example.invalid/openSections.json",
        observed_at=datetime(2026, 11, 4, 2, 30, tzinfo=UTC), verdict="accepted",
        content_sha256=sha or (hashlib.sha256(body).hexdigest() if body else None),
        archive_path=path)
    session.add(row)
    session.flush()
    return row


def test_missing_archives_are_repointed_or_marked(session, tmp_path) -> None:
    raw = tmp_path / "raw"
    live = raw / "live"
    live.mkdir(parents=True)
    same = b'["00001","00002"]'
    (live / "openSections_2027_0_NB_20261103T000753Z.json").write_bytes(same)
    (live / "openSections_2027_0_NB_20261104T020000Z.json").write_bytes(same)
    kept = live / "openSections_2027_0_NB_20261105T000000Z.json"
    kept.write_bytes(b'["00003"]')

    repointed = _row(session, "/data/raw/availability/openSections_2027_0_NB_20261104T023041Z.json",
                     same)
    lost = _row(session, "/data/raw/availability/openSections_2027_0_NB_20261107T193340Z.json",
                None, sha="f" * 64)
    present = _row(session, "/data/raw/live/openSections_2027_0_NB_20261105T000000Z.json",
                   b'["00003"]')
    host_style = _row(session, str(kept), b'["00003"]')

    dry = repair_missing_archives(session, host_raw=raw, search_dirs=[live], repo_root=tmp_path)
    assert dry.checked == 4 and dry.present == 2
    assert [r.action for r in dry.repairs] == ["repointed", "marked_missing"]
    assert repointed.archive_path.startswith("/data/raw/availability/")      # dry run: untouched

    done = repair_missing_archives(session, host_raw=raw, search_dirs=[live], repo_root=tmp_path,
                                   apply=True, now=datetime(2026, 11, 8, tzinfo=UTC))
    # The same bytes, nearest in time; stored in the row's own (container) style.
    assert repointed.archive_path == "/data/raw/live/openSections_2027_0_NB_20261104T020000Z.json"
    assert done.repairs[0].new_path == repointed.archive_path
    assert lost.archive_path is None
    assert "archive file missing: /data/raw/availability/" in lost.detail
    assert present.archive_path.endswith("20261105T000000Z.json")
    assert host_style.archive_path == str(kept)
    # Every remaining path exists; no file was written, moved or deleted.
    assert sorted(p.name for p in live.iterdir()) == [
        "openSections_2027_0_NB_20261103T000753Z.json",
        "openSections_2027_0_NB_20261104T020000Z.json",
        "openSections_2027_0_NB_20261105T000000Z.json"]
    again = repair_missing_archives(session, host_raw=raw, search_dirs=[live], repo_root=tmp_path)
    assert again.repairs == [] and again.checked == 3
