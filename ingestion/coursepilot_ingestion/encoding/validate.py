"""Validate an encoding against its archived sources (encoding pipeline, step 3).

Machine checks only - NOT review. They prove that:

  * the file has the shape the loaders accept;
  * every cited source is a manifest capture whose bytes are present and
    match their recorded sha256 (a failed capture cannot be quoted);
  * every quote appears verbatim (modulo whitespace, typographic quotes and
    dashes) in the text of the capture it cites - a quote that is not on the
    page means the encoding rests on text the source does not contain;
  * every node that names courses or certifications quotes something;
  * every `unresolved_conflicts` id is declared in the file's `conflicts`;
  * (with a database session) every named course exists - reported, never
    created - and every linked component is loaded.

Two file kinds: components (`"format": "component/1"`) and program
definitions. A legacy single-source program definition (one catalog page,
`source_prose` per node) is checked by program_registry.validate_definition
as before; one with `sources`/`quotes` is checked here.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

from coursepilot_ingestion.encoding.sources import load_manifest, source_text
from coursepilot_ingestion.program_registry import _norm

#: Sources that are not archived pages but database facts with their own
#: provenance (offering_core_code rows carry the SOC payload's data_source).
DB_SOURCES = {"soc-core-code-descriptions"}


@dataclass
class EncodingReport:
    path: str
    kind: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    quotes_checked: int = 0
    quotes_found: int = 0
    per_node: dict[str, list[dict]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def file_kind(definition: dict) -> str:
    return "component" if definition.get("format", "").startswith("component/") else "program"


def _nodes(definition: dict):
    yield from definition.get("requirements", [])


def _quoted_items(definition: dict):
    """(where, quote dict) for every quote the file makes."""
    kind = file_kind(definition)
    if kind == "component":
        total = definition["component"].get("degree_credits")
        if total:
            yield "component.degree_credits", {"source": total.get("capture"),
                                               "quote": total.get("quote")}
    else:
        total = definition.get("program_version", {}).get("degree_credits")
        if total:
            yield "program_version.degree_credits", {"source": total.get("capture"),
                                                     "quote": total.get("quote")}
        for q in definition.get("program_version", {}).get("quotes", []):
            yield "program_version", q
    # A multi-source PROGRAM may keep its catalog `source_prose` per node; it
    # is checked against the source marked "primary" (the catalog page).
    primary = next((s["id"] for s in definition.get("sources", []) if s.get("primary")), None)
    version_prose = definition.get("program_version", {}).get("source_prose")
    if kind == "program" and primary and version_prose:
        yield "program_version", {"source": primary, "quote": version_prose}
    for r in _nodes(definition):
        if kind == "program" and primary and r.get("source_prose"):
            yield r["code"], {"source": primary, "quote": r["source_prose"]}
        for q in r.get("quotes", []):
            yield r["code"], q
    for r in definition.get("school_rules", []) + definition.get("program_rules", []):
        if kind == "program" and primary and r.get("source_prose"):
            yield r["code"], {"source": primary, "quote": r["source_prose"]}
        for q in r.get("quotes", []):
            yield r["code"], q
    for c in definition.get("conflicts", []):
        for q in c.get("claims", []):
            yield f"conflict {c.get('id')}", q
    for r in definition.get("rules_not_yet_modeled", []):
        if r.get("quote"):
            yield "rules_not_yet_modeled", {"source": r.get("source"), "quote": r["quote"]}


def validate_file(path: pathlib.Path, session=None, manifest: dict | None = None) -> EncodingReport:
    definition = json.loads(path.read_bytes())
    kind = file_kind(definition)
    rep = EncodingReport(path=str(path), kind=kind)
    manifest = manifest if manifest is not None else load_manifest()

    if kind == "component":
        from coursepilot_ingestion.loaders.components import validate_component_shape

        rep.errors.extend(validate_component_shape(definition))
    elif not definition.get("sources"):
        # Legacy single-source program definition.
        from coursepilot_ingestion.program_registry import (
            ARCHIVE_DIR,
            archive_name,
            validate_definition,
        )
        year = definition["program_version"]["catalog_year"]
        archive = ARCHIVE_DIR / archive_name(year, definition["curation"]["catalog_page"])
        html = archive.read_text(encoding="utf-8") if archive.exists() else None
        legacy = validate_definition(definition, html, session)
        rep.errors.extend(legacy.errors)
        rep.warnings.extend(legacy.warnings)
        rep.quotes_checked, rep.quotes_found = legacy.quotes_checked, legacy.quotes_found
        return rep

    cited = {s["id"] for s in definition.get("sources", [])}
    texts: dict[str, str | None] = {}
    for sid in cited - DB_SOURCES:
        cap = manifest.get(sid)
        if cap is None:
            rep.errors.append(f"source {sid!r} is not in data/programs/rutgers/sources.json")
            continue
        if cap.get("representation") == "failed":
            texts[sid] = None
            continue
        try:
            text = source_text(cap)
        except ValueError as exc:
            rep.errors.append(str(exc))
            text = None
        if text is None:
            rep.warnings.append(f"source {sid!r}: archived bytes not present locally; "
                                "its quotes cannot be checked here")
        texts[sid] = _norm(text) if text else None

    db_descriptions = None
    for where, q in _quoted_items(definition):
        sid, quote = q.get("source"), q.get("quote")
        rep.per_node.setdefault(where, []).append({"source": sid, "quote": quote})
        if not quote:
            continue
        if sid not in cited:
            rep.errors.append(f"{where}: quotes source {sid!r}, which the file does not list")
            continue
        if sid in DB_SOURCES:
            if session is not None:
                if db_descriptions is None:
                    from app.models.core_codes import OfferingCoreCode
                    from sqlalchemy import select
                    db_descriptions = {_norm(f"{c}: {d}") for c, d in session.execute(
                        select(OfferingCoreCode.code, OfferingCoreCode.description).distinct())}
                rep.quotes_checked += 1
                if _norm(quote) in db_descriptions:
                    rep.quotes_found += 1
                else:
                    rep.errors.append(f"{where}: {quote!r} is not a SOC core-code description")
            continue
        cap = manifest.get(sid) or {}
        if cap.get("representation") == "failed":
            rep.errors.append(f"{where}: quotes {sid!r}, a FAILED capture - nothing to quote")
            continue
        text = texts.get(sid)
        if text is None:
            continue
        for part in (p for p in quote.split("...") if p.strip()):
            rep.quotes_checked += 1
            if _norm(part).strip(" .;,") in text:
                rep.quotes_found += 1
            else:
                rep.errors.append(f"{where}: quote not found verbatim in {sid}: "
                                  f"{part.strip()[:90]!r}")

    declared = {c.get("id") for c in definition.get("conflicts", [])}
    by_code = {r["code"]: r for r in _nodes(definition)}
    for r in _nodes(definition):
        for cid in r.get("unresolved_conflicts") or ():
            if cid not in declared:
                rep.errors.append(f"{r['code']}: conflict {cid!r} is not declared in 'conflicts'")
        names_courses = (r.get("courses") or r.get("course_categories")
                         or r.get("eligible_course_query") or r.get("certification_codes"))
        quoted_by = quote_owner(r, by_code)
        if names_courses and quoted_by is None:
            rep.errors.append(f"{r['code']}: names eligible courses but neither it nor an "
                              "ancestor quotes a source")
        if quoted_by is None:
            rep.warnings.append(f"{r['code']}: no quote (structural node?)")
        if r["requirement_type"] in ("course", "choose_n") and not names_courses \
                and not r.get("course_list_unknown"):
            rep.errors.append(f"{r['code']}: {r['requirement_type']} names no eligible courses "
                              "(set course_list_unknown with a reason if the source defers)")

    if session is not None:
        _check_database(definition, session, rep)
    return rep


def quote_owner(node: dict, by_code: dict) -> str | None:
    """The code of the nearest node (itself first, then ancestors) that quotes
    a source: a leaf "01:198:205" is covered by its parent's quoted list."""
    seen = set()
    while node is not None and node["code"] not in seen:
        seen.add(node["code"])
        if node.get("quotes") or node.get("source_prose"):
            return node["code"]
        node = by_code.get(node.get("parent"))
    return None


def _check_database(definition: dict, session, rep: EncodingReport) -> None:
    from app.models import Course, SchoolRequirementSetVersion
    from sqlalchemy import select

    known = set(session.scalars(select(Course.course_string).where(Course.supplement_code == "")))
    for r in _nodes(definition):
        for c in (r.get("courses") or []) + [x for v in (r.get("course_categories") or {}).values()
                                             for x in v]:
            if c not in known:
                rep.warnings.append(f"{r['code']}: {c} not in the loaded course table "
                                    "(reported, never created)")
    for code in definition.get("components", []):
        if session.scalar(select(SchoolRequirementSetVersion.id).where(
                SchoolRequirementSetVersion.code == code)) is None:
            rep.errors.append(f"linked component {code!r} is not loaded")


__all__ = ["DB_SOURCES", "EncodingReport", "file_kind", "quote_owner", "validate_file"]
