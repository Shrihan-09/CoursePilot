"""Repair availability refresh rows whose archive file no longer exists.

`availability_refresh.archive_path` records the openSections payload a refresh
read. Before Prompt 11 Part A, the term watcher archived into a container-only
cache (`/data/raw/availability`), so some rows name a file that never reached
the mounted `data/raw` folder. Nothing may point at a file that does not exist:

    the file exists                        -> left alone
    missing, same bytes archived elsewhere -> repointed to that file (the
                                              same content_sha256, so the row
                                              still describes what was received)
    missing, no file with those bytes      -> archive_path cleared and the
                                              old path kept in `detail`

The tool reads and hashes archives; it never writes, moves or deletes a file.
Dry run by default; `--apply` commits the row updates.

    python -m coursepilot_ingestion.archive_repair            # report only
    python -m coursepilot_ingestion.archive_repair --apply
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.models.availability import AvailabilityRefresh
from sqlalchemy import select
from sqlalchemy.orm import Session

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
#: The compose mount: the container's /data/raw/live is the host's data/raw/live.
CONTAINER_RAW = "/data/raw/"
_STAMP = re.compile(r"^(openSections_\d{4}_\d_[A-Z]{2}_)(\d{8}T\d{6}Z)\.json$")


@dataclass
class Repair:
    refresh_id: str
    old_path: str
    action: str                       # repointed | marked_missing
    new_path: str | None = None


@dataclass
class RepairReport:
    checked: int = 0
    present: int = 0
    repairs: list[Repair] = field(default_factory=list)


def host_path(stored: str, host_raw: pathlib.Path, repo_root: pathlib.Path) -> pathlib.Path:
    """Where a stored archive path lives on this machine."""
    if stored.startswith(CONTAINER_RAW):
        return host_raw / stored[len(CONTAINER_RAW):]
    p = pathlib.Path(stored)
    return p if p.is_absolute() else repo_root / p


def stored_form(found: pathlib.Path, original: str, host_raw: pathlib.Path) -> str:
    """Write the replacement in the same style as the row's original path."""
    if original.startswith(CONTAINER_RAW):
        return CONTAINER_RAW + found.resolve().relative_to(host_raw.resolve()).as_posix()
    return str(found)


def _stamp(name: str) -> datetime | None:
    m = _STAMP.match(name)
    return datetime.strptime(m.group(2), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC) if m else None


class _Index:
    """sha256 -> archives, hashed lazily per file-name prefix (term + campus)."""

    def __init__(self, dirs: list[pathlib.Path]) -> None:
        self.dirs = dirs
        self._by_prefix: dict[str, dict[str, list[pathlib.Path]]] = {}

    def same_bytes(self, name: str, sha: str) -> list[pathlib.Path]:
        m = _STAMP.match(name)
        prefix = m.group(1) if m else ""
        if prefix not in self._by_prefix:
            table: dict[str, list[pathlib.Path]] = {}
            for d in self.dirs:
                for f in sorted(d.glob(f"{prefix}*.json")) if d.is_dir() else []:
                    if f.is_file() and not f.is_symlink():
                        table.setdefault(hashlib.sha256(f.read_bytes()).hexdigest(), []).append(f)
            self._by_prefix[prefix] = table
        return self._by_prefix[prefix].get(sha, [])


def repair_missing_archives(session: Session, *, host_raw: pathlib.Path,
                            search_dirs: list[pathlib.Path], repo_root: pathlib.Path = REPO_ROOT,
                            apply: bool = False, now: datetime | None = None) -> RepairReport:
    report = RepairReport()
    index = _Index(search_dirs)
    now = now or datetime.now(UTC)
    rows = session.scalars(select(AvailabilityRefresh)
                           .where(AvailabilityRefresh.archive_path.is_not(None))
                           .order_by(AvailabilityRefresh.observed_at)).all()
    for row in rows:
        report.checked += 1
        stored = row.archive_path or ""
        if host_path(stored, host_raw, repo_root).is_file():
            report.present += 1
            continue
        name = re.split(r"[\\/]", stored)[-1]
        candidates = index.same_bytes(name, row.content_sha256) if row.content_sha256 else []
        if candidates:
            want = _stamp(name)
            best = min(candidates, key=lambda f: (
                abs((_stamp(f.name) - want).total_seconds()) if want and _stamp(f.name) else 0,
                f.name))
            new = stored_form(best, stored, host_raw)
            report.repairs.append(Repair(str(row.id), stored, "repointed", new))
            if apply:
                row.archive_path = new
        else:
            report.repairs.append(Repair(str(row.id), stored, "marked_missing"))
            if apply:
                note = f"archive file missing: {stored} (checked {now:%Y-%m-%d})"
                row.detail = f"{row.detail} | {note}" if row.detail else note
                row.archive_path = None
    if apply:
        session.flush()
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--apply", action="store_true", help="commit the row updates")
    p.add_argument("--host-raw", type=pathlib.Path, default=REPO_ROOT / "data" / "raw",
                   help="host folder mounted at /data/raw (default: <repo>/data/raw)")
    args = p.parse_args(argv)

    from app.db.session import get_sync_sessionmaker

    dirs = [args.host_raw / "live", args.host_raw / "availability"]
    with get_sync_sessionmaker()() as session:
        report = repair_missing_archives(session, host_raw=args.host_raw, search_dirs=dirs,
                                         apply=args.apply)
        if args.apply:
            session.commit()
        else:
            session.rollback()
    print(f"checked {report.checked} rows; {report.present} archives present; "
          f"{len(report.repairs)} {'repaired' if args.apply else 'to repair'}")
    for r in report.repairs:
        print(f"  {r.action}: {r.old_path} -> {r.new_path or '(cleared)'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
