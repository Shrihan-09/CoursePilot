"""Human review packets for curated program definitions (Phase 6.3).

A packet is what a reviewer needs to approve or reject ONE definition
against ONE source text, in two forms built from the same data:

  * `<name>.review.json` - machine-readable: hashes, quote checks, per-node
    eligibility counts, open questions, the exact review command;
  * `<name>.review.md`   - the same, for a person to read and tick off.

The packet records facts and checks; it never records a decision. A
decision is made by a named human with `registry_cli review`, which stores
it in `program_review` against the hashes printed here.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

from app.models import Course, Requirement, RequirementCourseOption
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from coursepilot_ingestion.catalog_registry import prose_sha256
from coursepilot_ingestion.loaders.requirements import definition_sha256
from coursepilot_ingestion.program_registry import (
    ARCHIVE_DIR,
    _quotes,
    archive_name,
    find_version,
    validate_definition,
)

PACKET_VERSION = "review-packet/1"
GENERIC_CHECKS = [
    "Open the archived page (path below) and find every quoted sentence.",
    "For each requirement node, confirm the encoding says what the quote says: course lists, counts, levels, exclusions.",
    "Confirm nothing the page requires for COMPLETION is missing - compare against 'rules not yet modeled'.",
    "Confirm admission/declaration rules are kept as admission_prose and not as completion requirements.",
    "Answer every review question; a 'changes_requested' review is a valid outcome.",
    "Spot-check the eligible-course counts below against the course list you expect.",
]


def _program_key(d: dict) -> str:
    p = d["program"]
    parts = [d["school"]["code"], p["code"], p["degree_type"]] + ([p["variant"]] if p.get("variant") else [])
    return "-".join(x.lower() for x in parts)


def build_packet(path: pathlib.Path, session: Session | None = None) -> dict:
    d = json.loads(path.read_bytes())
    year = d["program_version"]["catalog_year"]
    archive = ARCHIVE_DIR / archive_name(year, d["curation"]["catalog_page"])
    html = archive.read_text(encoding="utf-8") if archive.exists() else None
    report = validate_definition(d, html, session)
    nodes = []
    counts = {}
    version = find_version(session, d) if session is not None else None
    if version is not None:
        counts = dict(session.execute(
            select(Requirement.code, func.count(RequirementCourseOption.id))
            .outerjoin(RequirementCourseOption, RequirementCourseOption.requirement_id == Requirement.id)
            .where(Requirement.program_version_id == version.id)
            .group_by(Requirement.code)).all())
    for r in d["requirements"]:
        node = {"code": r["code"], "type": r["requirement_type"], "parent": r.get("parent"),
                "quote": r.get("source_prose"), "notes": r.get("notes")}
        for key in ("min_count", "min_distinct_categories", "min_at_level", "min_at_level_count",
                    "max_outside_subject", "constraint_subject_code"):
            if r.get(key) is not None:
                node[key] = r[key]
        if r.get("courses"):
            node["courses"] = r["courses"]
        if r.get("course_categories"):
            node["course_categories"] = r["course_categories"]
        if r.get("eligible_course_query"):
            node["eligible_course_query"] = r["eligible_course_query"]
        if r["code"] in counts:
            node["eligible_course_rows"] = counts[r["code"]]
        nodes.append(node)
    if session is not None and version is not None:
        grad = session.scalar(
            select(func.count()).select_from(RequirementCourseOption)
            .join(Requirement, Requirement.id == RequirementCourseOption.requirement_id)
            .join(Course, Course.id == RequirementCourseOption.course_id)
            .where(Requirement.program_version_id == version.id,
                   Course.offering_unit_code != "01",
                   Requirement.eligibility_rule.is_not(None)))
    else:
        grad = None
    return {
        "packet_version": PACKET_VERSION,
        "program_key": _program_key(d),
        "catalog_year": year,
        "definition_file": str(path.relative_to(ARCHIVE_DIR.parents[2])).replace("\\", "/"),
        "definition_sha256": definition_sha256(d),
        "source": {
            "url": d["source"]["url"],
            "catalog_page": d["curation"]["catalog_page"],
            "archive": str(archive.relative_to(ARCHIVE_DIR.parents[2])).replace("\\", "/"),
            "archive_present": html is not None,
            "archive_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest() if html else None,
            "prose_sha256": prose_sha256(html) if html else None,
        },
        "curated_by": d.get("curation", {}).get("curated_by"),
        "curation_status_label": d["source"].get("curation_status"),
        "lifecycle_state": version.lifecycle_state if version is not None else None,
        "publication_basis": version.publication_basis if version is not None else None,
        "machine_checks": {
            "passed": report.ok,
            "quotes_found": report.quotes_found,
            "quotes_checked": report.quotes_checked,
            "errors": report.errors,
            "warnings": report.warnings,
            "note": "Machine checks are NOT review. They prove quotes exist and the engine can "
                    "load the tree - not that the encoding means what the prose means.",
        },
        "non_undergraduate_eligible_rows": grad,
        "admission_prose": d["program_version"].get("admission_prose"),
        "requirements": nodes,
        "program_rules": d.get("program_rules", []),
        "rules_not_yet_modeled": d.get("rules_not_yet_modeled", []),
        "review_questions": d.get("review_questions", []),
        "what_to_verify": GENERIC_CHECKS,
        "how_to_record": (
            "python -m coursepilot_ingestion.registry_cli review "
            f"--program {_program_key(d)} --year {year} --reviewer \"<your full name>\" "
            "--decision approved|changes_requested --notes \"...\"  "
            "(then, if approved: registry_cli publish --program ... --year ...)"),
        "quotes": [{"node": where, "quote": q} for where, q in _quotes(d) if q],
    }


def to_markdown(p: dict) -> str:
    mc = p["machine_checks"]
    lines = [
        f"# Review packet: `{p['program_key']}` ({p['catalog_year']})",
        "",
        f"- **Lifecycle:** `{p['lifecycle_state']}`"
        + (f" (basis `{p['publication_basis']}`)" if p["publication_basis"] else ""),
        f"- **Encoded by:** {p['curated_by']} - encoding is not review",
        f"- **Definition:** `{p['definition_file']}`  sha256 `{p['definition_sha256'][:16]}`",
        f"- **Source:** {p['source']['url']}",
        f"- **Archive:** `{p['source']['archive']}`"
        + (f"  prose sha256 `{p['source']['prose_sha256'][:16]}`" if p["source"]["prose_sha256"] else
           "  (NOT PRESENT locally)"),
        "",
        "## Machine checks (not review)",
        "",
        f"- passed: **{mc['passed']}**, quotes found verbatim: {mc['quotes_found']}/{mc['quotes_checked']}",
    ]
    lines += [f"- ERROR: {e}" for e in mc["errors"]]
    if mc["warnings"]:
        lines.append(f"- {len(mc['warnings'])} course(s) named by the definition are not in the "
                     "loaded course table (reported, never created), e.g. "
                     + ", ".join(w.split(": ")[1].split(" ")[0] for w in mc["warnings"][:6]))
    if p["non_undergraduate_eligible_rows"]:
        lines.append(f"- {p['non_undergraduate_eligible_rows']} eligible row(s) are courses outside "
                     "offering unit 01 (graduate 16:xxx, or another school's course) - check each was "
                     "meant: named explicitly, or admitted by a query")
    lines += ["", "## Review questions", ""]
    lines += [f"- [ ] {q}" for q in p["review_questions"]] or ["- (none recorded)"]
    lines += ["", "## What to verify", ""] + [f"- [ ] {c}" for c in p["what_to_verify"]]
    if p["admission_prose"]:
        lines += ["", "## Admission (kept separate from completion)", "", f"> {p['admission_prose']}"]
    lines += ["", "## Requirement nodes", "",
              "| node | type | parent | encoding | eligible rows |", "|---|---|---|---|---|"]
    for n in p["requirements"]:
        enc = []
        for k in ("min_count", "min_distinct_categories", "min_at_level", "min_at_level_count",
                  "max_outside_subject", "constraint_subject_code"):
            if k in n:
                enc.append(f"{k}={n[k]}")
        if "courses" in n:
            enc.append("courses: " + ", ".join(n["courses"]))
        if "course_categories" in n:
            enc.append("; ".join(f"{k}: {', '.join(v)}" for k, v in n["course_categories"].items()))
        if "eligible_course_query" in n:
            enc.append("query " + json.dumps(n["eligible_course_query"], sort_keys=True))
        lines.append(f"| `{n['code']}` | {n['type']} | {n['parent'] or ''} | "
                     f"{'<br>'.join(enc).replace('|', '/')} | {n.get('eligible_course_rows', '')} |")
    lines += ["", "### Quotes and notes", ""]
    for n in p["requirements"]:
        if n["quote"] or n["notes"]:
            lines.append(f"- **{n['code']}**" + (f": \"{n['quote']}\"" if n["quote"] else ""))
            if n["notes"]:
                lines.append(f"  - note: {n['notes']}")
    if p["program_rules"]:
        lines += ["", "## Program rules", ""]
        lines += [f"- `{r['code']}` ({r['rule_type']}, evaluable={r.get('is_evaluable', True)}): "
                  f"\"{r.get('source_prose')}\"" for r in p["program_rules"]]
    lines += ["", "## Rules not yet modeled", ""]
    lines += [f"- {r['rule']}  \n  _why:_ {r['why_not']}" for r in p["rules_not_yet_modeled"]] or ["- (none)"]
    lines += ["", "## How to record your decision", "", f"```\n{p['how_to_record']}\n```", ""]
    return "\n".join(lines)


def write_packets(files: list[pathlib.Path], out_dir: pathlib.Path,
                  session: Session | None = None) -> list[pathlib.Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for f in files:
        packet = build_packet(f, session)
        stem = f.stem
        (out_dir / f"{stem}.review.json").write_text(
            json.dumps(packet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (out_dir / f"{stem}.review.md").write_text(to_markdown(packet), encoding="utf-8")
        written.append(out_dir / f"{stem}.review.md")
    return written


__all__ = ["PACKET_VERSION", "build_packet", "to_markdown", "write_packets"]
