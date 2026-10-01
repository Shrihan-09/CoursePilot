"""Program registry operations: discover, fetch, link, validate, check (Phase 6.3).

```
discover_year   archived page of a year  -> catalog_page rows            DISCOVERED
fetch_page      public catalog page      -> archive + data_source         FETCHED
                                         -> program_candidate rows        PARSED (claims)
link_definition curated definition       -> version.catalog_page/snapshot/prose hash
validate        deterministic checks     -> lifecycle `validated`  (never `reviewed`)
check_sources   latest page prose        -> `needs_rereview` when it moved
```

Source rules (binding, see docs/DATA_SOURCES.md):

  * Only the PUBLIC Rutgers catalog is fetched, one page at a time, with the
    project User-Agent and a pause between requests. Nothing authenticated -
    no Degree Navigator, CSP, WebReg or student data.
  * Page URLs come from Rutgers' own navigation tree (catalog_page rows),
    never composed from a program name.
  * A fetched page is archived before it is parsed, so every hash and every
    candidate can be re-derived offline.

Nothing here reviews or publishes. Validation reaches `validated`; review
and publication are human acts recorded through app.services.program_lifecycle.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.models import (
    CatalogPage,
    Course,
    DataSource,
    Program,
    ProgramCandidate,
    ProgramVersion,
    Requirement,
    School,
    Student,
)
from app.services import program_lifecycle
from app.services.audit.engine import DegreeAuditEngine
from app.services.eligibility import rule_from_definition
from sqlalchemy import select
from sqlalchemy.orm import Session

from coursepilot_ingestion.catalog_registry import CatalogRegistry, page_prose
from coursepilot_ingestion.loaders.requirements import definition_sha256
from coursepilot_ingestion.sources.catalog import CATALOG_HOSTS, SOURCE_KIND, USER_AGENT

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
ARCHIVE_DIR = ROOT / "data" / "raw" / "catalog"
CURATED_ROOT = ROOT / "data" / "programs" / "rutgers"
#: Seconds between live requests. The catalog is a public site run by the
#: university; a planning tool has no reason to load it faster than a reader.
POLITE_DELAY = 2.0
REQUIREMENT_TYPES = {"all_of", "any_of", "choose_n", "credits", "course"}
#: requirement_course_option.category is String(16).
CATEGORY_MAX = 16


def archive_name(catalog_year: str, url_path: str) -> str:
    """`/schools/sas/program-listing/mathematics-640` -> catalog_mathematics-640_2026_2027.html.

    Same naming as Phases 3-6.1, so pages archived then are reused, not refetched.
    """
    slug = url_path.rstrip("/").rsplit("/", 1)[-1]
    return f"catalog_{slug}_{catalog_year.replace('-', '_')}.html"


# --------------------------------------------------------------------------
# discover / fetch
# --------------------------------------------------------------------------


def discover_year(session: Session, catalog_year: str, page_html: str,
                  now: datetime | None = None):
    return CatalogRegistry(session).discover(page_html, catalog_year, now)


def _snapshot(session: Session, page: CatalogPage, html: str, url: str,
              retrieved_at: datetime, archive: pathlib.Path) -> DataSource:
    content_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
    existing = session.scalar(select(DataSource).where(DataSource.content_hash == content_hash,
                                                       DataSource.kind == SOURCE_KIND))
    if existing is not None:
        return existing
    source = DataSource(kind=SOURCE_KIND, url=url, title=f"{page.title} ({page.catalog_year})",
                        retrieved_at=retrieved_at, content_hash=content_hash,
                        academic_year=page.catalog_year, raw_payload_ref=str(archive),
                        record_count=1)
    session.add(source)
    session.flush()
    return source


def fetch_page(session: Session, page: CatalogPage, *, archive_dir: pathlib.Path = ARCHIVE_DIR,
               allow_network: bool = False, client=None, now: datetime | None = None):
    """Archive (or reuse the archive of) one catalog page and record it.

    `allow_network=False` (the default) uses archives only - tests and
    re-processing never touch the network.
    """
    archive = archive_dir / archive_name(page.catalog_year, page.url_path)
    url = CATALOG_HOSTS[page.catalog_year] + page.url_path
    if archive.exists():
        html = archive.read_text(encoding="utf-8")
        retrieved_at = datetime.fromtimestamp(archive.stat().st_mtime, tz=UTC)
    elif not allow_network:
        raise FileNotFoundError(f"{archive.name} is not archived and network is disabled")
    else:
        import httpx

        time.sleep(POLITE_DELAY)
        own = client is None
        client = client or httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT},
                                        follow_redirects=True)
        try:
            response = client.get(url)
            response.raise_for_status()
        finally:
            if own:
                client.close()
        html = response.text
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive.write_text(html, encoding="utf-8")
        retrieved_at = datetime.now(UTC)
    snapshot = _snapshot(session, page, html, url, retrieved_at, archive)
    stats = CatalogRegistry(session).record_fetch(page, html, snapshot.id, now)
    return html, stats


# --------------------------------------------------------------------------
# curated definitions <-> registry
# --------------------------------------------------------------------------


def curated_files(root: pathlib.Path = CURATED_ROOT) -> list[pathlib.Path]:
    """Every curated program definition (SAS Core is a different shape)."""
    return sorted(p for p in root.rglob("*.json")
                  if "review" not in p.parent.parts[-1:] and not p.name.endswith(".review.json")
                  and "program" in json.loads(p.read_bytes()))


def find_version(session: Session, definition: dict) -> ProgramVersion | None:
    pdef, vdef = definition["program"], definition["program_version"]
    return session.scalar(
        select(ProgramVersion)
        .join(Program, Program.id == ProgramVersion.program_id)
        .join(School, School.id == Program.school_id)
        .where(School.code == definition["school"]["code"], Program.code == pdef["code"],
               Program.degree_type == pdef["degree_type"],
               Program.variant == pdef.get("variant", ""),
               ProgramVersion.catalog_year == vdef["catalog_year"]))


@dataclass(slots=True)
class LinkResult:
    page_found: bool = False
    fetched: bool = False
    candidate_linked: bool = False
    source_check: str | None = None
    state: str | None = None


def link_definition(session: Session, definition: dict, version: ProgramVersion,
                    now: datetime | None = None) -> LinkResult:
    """Point a loaded version at its catalog page, snapshot and candidate,
    and compare the page's current prose with what the version rests on."""
    out = LinkResult()
    cdef = definition.get("curation", {})
    page = session.scalar(select(CatalogPage).where(
        CatalogPage.catalog_year == version.catalog_year,
        CatalogPage.url_path == cdef.get("catalog_page")))
    if page is None:
        return out
    out.page_found = True
    version.catalog_page_id = page.id
    if page.snapshot_id is None:
        return out
    out.fetched = True
    version.source_snapshot_id = page.snapshot_id
    candidate = session.scalar(select(ProgramCandidate).where(
        ProgramCandidate.catalog_page_id == page.id,
        ProgramCandidate.candidate_key == cdef.get("candidate_key")))
    if candidate is not None:
        candidate.program_version_id = version.id
        out.candidate_linked = True
    out.source_check = program_lifecycle.check_source(version, page.prose_sha256, now)
    out.state = version.lifecycle_state
    session.flush()
    return out


def check_sources(session: Session, now: datetime | None = None) -> dict[str, str]:
    """Every linked version against its page's latest prose hash."""
    out = {}
    rows = session.execute(
        select(ProgramVersion, CatalogPage)
        .join(CatalogPage, CatalogPage.id == ProgramVersion.catalog_page_id)
        .where(CatalogPage.prose_sha256.is_not(None))).all()
    for version, page in rows:
        result = program_lifecycle.check_source(version, page.prose_sha256, now)
        out[f"{page.url_path} {version.catalog_year}"] = f"{result} -> {version.lifecycle_state}"
    session.flush()
    return out


# --------------------------------------------------------------------------
# deterministic validation - NOT review
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    quotes_checked: int = 0
    quotes_found: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def _norm(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"')
    text = text.replace("”", '"').replace("–", "-").replace("—", "-")
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    # Stripping inline markup leaves "381 , 382" where the page shows "381, 382".
    return re.sub(r" ([,.;:)])", r"\1", text).strip().lower()


def _quotes(definition: dict):
    yield "program_version", definition["program_version"].get("source_prose")
    yield "program_version.admission", definition["program_version"].get("admission_prose")
    for r in definition.get("requirements", []):
        yield r["code"], r.get("source_prose")
    for r in definition.get("program_rules", []):
        yield r["code"], r.get("source_prose")


def validate_definition(definition: dict, page_html: str | None,
                        session: Session | None = None) -> ValidationReport:
    """Schema, verbatim quotes against the archived page, references, engine load.

    Every `source_prose` (split on the curator's " ... " elisions) must
    appear verbatim - modulo whitespace, typographic quotes and dashes - in
    the archived page's prose. A quote that is not on the page means the
    definition rests on text the source does not contain.
    """
    rep = ValidationReport()
    for key in ("source", "school", "program", "program_version", "requirements"):
        if key not in definition:
            rep.errors.append(f"missing top-level key {key!r}")
    if rep.errors:
        return rep
    codes = [r["code"] for r in definition["requirements"]]
    if len(codes) != len(set(codes)):
        rep.errors.append("duplicate requirement codes")
    roots = [r for r in definition["requirements"] if not r.get("parent")]
    if len(roots) != 1:
        rep.errors.append(f"expected exactly one root requirement, found {len(roots)}")
    for r in definition["requirements"]:
        if r["requirement_type"] not in REQUIREMENT_TYPES:
            rep.errors.append(f"{r['code']}: unknown requirement_type {r['requirement_type']!r}")
        if r.get("parent") and r["parent"] not in codes:
            rep.errors.append(f"{r['code']}: parent {r['parent']!r} does not exist")
        if r["requirement_type"] == "choose_n" and not r.get("min_count"):
            rep.errors.append(f"{r['code']}: choose_n without min_count")
        if r["requirement_type"] in ("course", "choose_n") and rule_from_definition(r) is None:
            rep.errors.append(f"{r['code']}: {r['requirement_type']} names no eligible courses")
        try:
            rule_from_definition(r)
        except ValueError as exc:
            rep.errors.append(f"{r['code']}: {exc}")
        for category in r.get("course_categories", {}):
            if len(category) > CATEGORY_MAX:
                rep.errors.append(f"{r['code']}: category {category!r} exceeds "
                                  f"{CATEGORY_MAX} characters")
    if not definition.get("curation", {}).get("catalog_page"):
        rep.errors.append("curation.catalog_page missing: provenance cannot be linked")

    if page_html is None:
        rep.errors.append("no archived source page: quotes cannot be verified")
    else:
        prose = _norm(page_prose(page_html))
        for where, quote in _quotes(definition):
            if not quote:
                continue
            for part in (p for p in quote.split("...") if p.strip()):
                rep.quotes_checked += 1
                if _norm(part).strip(" .;,") in prose:
                    rep.quotes_found += 1
                else:
                    rep.errors.append(f"{where}: quote not found verbatim on the page: "
                                      f"{part.strip()[:90]!r}")

    if session is not None:
        _check_against_database(definition, session, rep)
    return rep


def _check_against_database(definition: dict, session: Session, rep: ValidationReport) -> None:
    known = set(session.scalars(select(Course.course_string)))
    for r in definition["requirements"]:
        named = list(r.get("courses", []))
        for strings in r.get("course_categories", {}).values():
            named.extend(strings)
        for cs in named:
            if cs not in known:
                rep.warnings.append(f"{r['code']}: {cs} not in the loaded course table")
    version = find_version(session, definition)
    if version is None:
        rep.errors.append("definition is not loaded: the engine cannot be exercised")
        return
    stored = session.scalar(select(Requirement.id).where(
        Requirement.program_version_id == version.id).limit(1))
    if stored is None:
        rep.errors.append("loaded version has no requirements")
        return
    # An empty, TRANSIENT student: proves the engine can evaluate the tree
    # without writing anything.
    probe = Student(id=uuid.uuid4(), catalog_year=version.catalog_year,
                    program_version_id=version.id)
    try:
        DegreeAuditEngine(session).audit(probe, program_version=version)
    except Exception as exc:                                   # noqa: BLE001
        rep.errors.append(f"engine could not evaluate the definition: {exc}")
    if session.new or session.dirty or session.deleted:
        rep.errors.append("engine probe left pending changes")


def validate_and_mark(session: Session, definition: dict, page_html: str | None,
                      now: datetime | None = None) -> ValidationReport:
    """Run validation; on success move the version to `validated` - no further."""
    rep = validate_definition(definition, page_html, session)
    version = find_version(session, definition)
    if rep.ok and version is not None:
        if version.definition_sha256 != definition_sha256(definition):
            rep.errors.append("loaded definition differs from this file; reload it first")
        elif version.lifecycle_state in (program_lifecycle.PARSED,
                                         program_lifecycle.NEEDS_REREVIEW):
            program_lifecycle.mark_validated(version, now)
    return rep


__all__ = ["ARCHIVE_DIR", "CURATED_ROOT", "LinkResult", "ValidationReport", "archive_name",
           "check_sources", "curated_files", "discover_year", "fetch_page", "find_version",
           "link_definition", "validate_and_mark", "validate_definition"]
