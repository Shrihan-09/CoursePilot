"""Reviewed Degree Navigator -> CoursePilot requirement-node mapping tables (Phase 7.4).

`<root>/<catalog year>/dn-mapping/<program key>.json` (answer 1 of the 7.4
decisions). A DN block is matched to a table by its printed subject EXACTLY
("The SAS Core Curriculum (NB)"); inside it a requirement maps by its exact
printed heading ("Requirement R1 : Contemporary Challenges [CC]") and a Core
goal by its exact code ("CCO"). Nothing is matched by similarity: a heading
the table does not list is `unmapped`, kept and shown, never guessed.

A mapping only says WHICH CoursePilot node a DN status belongs to. It never
changes a requirement rule.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass, field

#: Entries with these statuses are used; anything else (unmapped, rejected) is not.
USABLE_REVIEW_STATUSES = ("reviewed", "pending_review")


@dataclass(frozen=True)
class MappingEntry:
    requirement_code: str | None
    review_status: str
    reason: str | None = None

    @property
    def usable(self) -> bool:
        return self.requirement_code is not None and self.review_status in USABLE_REVIEW_STATUSES


@dataclass(frozen=True)
class DnMapping:
    program_key: str
    scope: str                      # program | school_core
    catalog_year: str
    dn_subject: str | None
    dn_versions: tuple[str, ...]
    mapping_version: str
    sha256: str
    requirements: dict[str, MappingEntry] = field(default_factory=dict)   # by heading
    goals: dict[str, MappingEntry] = field(default_factory=dict)          # by code

    def requirement(self, heading: str) -> MappingEntry | None:
        return self.requirements.get(_norm(heading))

    def goal(self, code: str) -> MappingEntry | None:
        return self.goals.get(code)


def _norm(text: str) -> str:
    """Whitespace only - case, punctuation and words are compared exactly."""
    return " ".join((text or "").split())


def mapping_root() -> pathlib.Path:
    from app.core.config import get_settings

    configured = get_settings().dn_mapping_root
    if configured:
        return pathlib.Path(configured)
    return pathlib.Path(__file__).resolve().parents[4] / "data" / "programs" / "rutgers" / \
        "nb-undergrad"


def load_mappings(catalog_year: str, root: pathlib.Path | None = None) -> list[DnMapping]:
    folder = (root or mapping_root()) / catalog_year / "dn-mapping"
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.json")):
        raw = path.read_bytes()
        data = json.loads(raw)
        out.append(DnMapping(
            program_key=data["program_key"], scope=data.get("scope", "program"),
            catalog_year=catalog_year, dn_subject=data.get("dn_subject"),
            dn_versions=tuple(data.get("dn_versions") or ()),
            mapping_version=str(data.get("mapping_version", "0")),
            sha256=hashlib.sha256(raw).hexdigest(),
            requirements={_norm(e["dn_heading"]): MappingEntry(
                e.get("requirement_code"), e.get("review_status", "pending_review"),
                e.get("reason")) for e in data.get("requirements", [])},
            goals={e["dn_code"]: MappingEntry(
                e.get("requirement_code"), e.get("review_status", "pending_review"),
                e.get("reason")) for e in data.get("goals", [])}))
    return out


@dataclass(frozen=True)
class TrackCheck:
    #: matches_track | matches_school_core | mismatch | no_mapping
    result: str
    message: str
    mapping: DnMapping | None
    #: True when confirmation must be refused (answer 4: a mismatch blocks).
    blocks: bool = False


def check_track(mappings: list[DnMapping], subject: str | None, version_label: str | None,
                track_key: str, track_name: str, school_core_keys: set[str]) -> TrackCheck:
    """Which table this DN block belongs to, and whether it is the student's."""
    found = next((m for m in mappings if m.dn_subject and subject
                  and _norm(m.dn_subject) == _norm(subject)), None)
    printed = subject or "an unnamed degree"
    if found is None:
        return TrackCheck("no_mapping", (
            f"CoursePilot has no reviewed Degree Navigator mapping for \"{printed}\", so it "
            "cannot check which program this audit is for, and none of its requirements is "
            "used for planning (all are shown as unmapped). Its courses can still be "
            "imported after your review."), None)
    if found.program_key == track_key:
        result, message = "matches_track", f"This audit is for your track ({track_name})."
    elif found.program_key in school_core_keys:
        result, message = "matches_school_core", f"This audit is your school's Core ({printed})."
    else:
        return TrackCheck("mismatch", (
            f"This audit is for {printed}, your CoursePilot track is {track_name}. Change your "
            "program in CoursePilot or import the matching Degree Navigator audit."), found,
            blocks=True)
    if version_label and found.dn_versions and version_label not in found.dn_versions:
        # Same program, but a DN version nobody has mapped: never assume the
        # headings mean the same thing.
        return TrackCheck(result, message + (
            f" Its requirement mapping was reviewed for Degree Navigator version(s) "
            f"{', '.join(found.dn_versions)}, not {version_label}, so every requirement is "
            "shown as unmapped."), None)
    return TrackCheck(result, message, found)


__all__ = ["USABLE_REVIEW_STATUSES", "DnMapping", "MappingEntry", "TrackCheck", "check_track",
           "load_mappings", "mapping_root"]
