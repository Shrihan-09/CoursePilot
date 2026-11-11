"""Review packets for multi-source encodings (encoding pipeline, step 4).

One packet per encoding file, in two forms built from the same data:

    <stem>.review.json   machine-readable
    <stem>.review.md     for a person to read and tick off

For EVERY node the packet shows the encoding (type, counts, courses, codes,
labels) next to the VERBATIM quote(s) it rests on - its own, or, for a leaf
course, the nearest ancestor's (marked "via"). Plus: every cited source with
URL, UTC time, sha256 and representation (failed captures listed as gaps),
machine checks, school rules, rules not yet modeled, unknowns, open
conflicts with both claims, and review questions.

A packet records facts and checks; it never records a decision.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

from coursepilot_ingestion.encoding.sources import ROOT, load_manifest, resolve
from coursepilot_ingestion.encoding.validate import (
    file_kind,
    quote_owner,
    validate_file,
)

PACKET_VERSION = "review-packet/3"      # 3: multi-source, quotes per node, components
NODE_KEYS = ("min_count", "min_credits", "min_distinct_categories", "min_at_level",
             "min_at_level_count", "max_outside_subject", "constraint_subject_code", "min_grade",
             "min_course_credits", "available_after_stage", "graded_only", "allocation_scope",
             "required_campus", "eligible_origins")
CHECKS = [
    "Open each archived source (paths below) and find every quoted sentence.",
    "For each node, confirm the encoding says what its quote says: courses, counts, levels, "
    "grade floors, credit minimums, stages.",
    "Confirm nothing the sources require is missing - compare with 'rules not yet modeled' and "
    "'unknowns'.",
    "Resolve or explicitly accept each OPEN conflict; never pick a side silently.",
    "Confirm admission/declaration rules are entry-stage requirements or labels, never "
    "completion requirements, and that no plan would assume admission.",
    "Answer every review question; 'changes requested' is a valid outcome.",
]


def _key(definition: dict) -> str:
    if file_kind(definition) == "component":
        return definition["component"]["code"]
    p = definition["program"]
    parts = [definition["school"]["code"], p["code"], p["degree_type"]] + (
        [p["variant"]] if p.get("variant") else []) + (
        [definition["program_version"]["curriculum_key"]]
        if definition["program_version"].get("curriculum_key") else [])
    return "-".join(x.lower() for x in parts)


def _semantic_sha(definition: dict) -> str:
    if file_kind(definition) == "component":
        from coursepilot_ingestion.loaders.components import component_sha256
        return component_sha256(definition)
    from coursepilot_ingestion.loaders.requirements import definition_sha256
    return definition_sha256(definition)


def build_packet(path: pathlib.Path, session=None) -> dict:
    definition = json.loads(path.read_bytes())
    kind = file_kind(definition)
    report = validate_file(path, session)
    manifest = load_manifest()
    by_code = {r["code"]: r for r in definition.get("requirements", [])}
    primary = next((s["id"] for s in definition.get("sources", []) if s.get("primary")), None)

    sources = []
    for s in definition.get("sources", []):
        cap = manifest.get(s["id"], {})
        sources.append({
            "id": s["id"], "role": s.get("role"), "note": s.get("note"),
            "url": cap.get("final_url") or cap.get("requested_url"),
            "retrieved_at": cap.get("retrieved_at"), "sha256": cap.get("sha256"),
            "representation": cap.get("representation"), "archive": cap.get("archive_path"),
            "archive_present": bool(resolve(cap)) if cap else False,
            "status": "failed (open evidence gap)" if cap.get("representation") == "failed"
            else ("not in manifest" if not cap else "archived")})

    counts = _eligible_counts(session, definition) if session is not None else {}
    nodes = []
    for r in definition.get("requirements", []):
        owner = quote_owner(r, by_code)
        quotes = []
        if owner is not None:
            src = by_code[owner]
            if src.get("source_prose") and primary:
                quotes.append({"source": primary, "quote": src["source_prose"]})
            quotes += [{"source": q["source"], "quote": q["quote"]} for q in src.get("quotes", [])]
        node = {"code": r["code"], "name": r["name"], "type": r["requirement_type"],
                "parent": r.get("parent"),
                "quotes": quotes, "quoted_via": owner if owner != r["code"] else None,
                "notes": r.get("notes")}
        for k in NODE_KEYS:
            if r.get(k) not in (None, False, [], ""):
                node[k] = r[k]
        for k in ("courses", "course_categories", "eligible_course_query", "certification_codes",
                  "group_caps", "unresolved_conflicts", "grade_quota", "category_sequences"):
            if r.get(k):
                node[k] = r[k]
        if r.get("course_list_unknown"):
            node["course_list_unknown"] = True
        if r["code"] in counts:
            node["eligible_course_rows"] = counts[r["code"]]
        nodes.append(node)

    lifecycle = _lifecycle(session, definition) if session is not None else None
    raw = path.read_bytes()
    rel = path.resolve().relative_to(ROOT).as_posix() if path.resolve().is_relative_to(ROOT) \
        else str(path)
    return {
        "packet_version": PACKET_VERSION,
        "key": _key(definition),
        "kind": kind,
        "component_kind": definition.get("component", {}).get("kind"),
        "definition_file": rel,
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "definition_sha256": _semantic_sha(definition),
        "curated_by": definition.get("curation", {}).get("curated_by"),
        "lifecycle_state": lifecycle,
        "sources": sources,
        "machine_checks": {
            "passed": report.ok, "quotes_found": report.quotes_found,
            "quotes_checked": report.quotes_checked, "errors": report.errors,
            "warnings": [w for w in report.warnings if "structural node" not in w],
            "note": "Machine checks are NOT review. They prove quotes exist verbatim in the "
                    "archived sources and the file loads - not that the encoding means what "
                    "the sources mean."},
        "requirements": nodes,
        "school_rules": definition.get("school_rules", []) + definition.get("program_rules", []),
        "rules_not_yet_modeled": definition.get("rules_not_yet_modeled", []),
        "unknowns": definition.get("unknowns", []),
        "conflicts": definition.get("conflicts", []),
        "review_questions": definition.get("review_questions", []),
        "what_to_verify": CHECKS,
        "how_to_record": (
            "python -m coursepilot_ingestion.registry_cli review --program "
            f"{_key(definition)} --year 2026-2027 --reviewer \"<your full name>\" "
            "--decision approved|changes_requested --notes \"...\""
            if kind == "program" else
            "Components have no review command yet: record the decision in the PR / review "
            "notes; the component stays 'pending_review' (and every audit and index label says "
            "so) until a component review command exists."),
    }


def _eligible_counts(session, definition: dict) -> dict:
    from app.models import (
        Requirement,
        RequirementCourseOption,
        SchoolRequirementSetVersion,
    )
    from sqlalchemy import func, select

    if file_kind(definition) == "component":
        owner = session.scalar(select(SchoolRequirementSetVersion.id).where(
            SchoolRequirementSetVersion.code == definition["component"]["code"]))
        cond = Requirement.school_requirement_set_version_id == owner
    else:
        from coursepilot_ingestion.program_registry import find_version
        version = find_version(session, definition)
        if version is None:
            return {}
        cond = Requirement.program_version_id == version.id
    return dict(session.execute(
        select(Requirement.code, func.count(RequirementCourseOption.id))
        .outerjoin(RequirementCourseOption, RequirementCourseOption.requirement_id == Requirement.id)
        .where(cond).group_by(Requirement.code)).all())


def _lifecycle(session, definition: dict):
    from app.models import SchoolRequirementSetVersion
    from sqlalchemy import select

    if file_kind(definition) == "component":
        return session.scalar(select(SchoolRequirementSetVersion.lifecycle_state).where(
            SchoolRequirementSetVersion.code == definition["component"]["code"]))
    from coursepilot_ingestion.program_registry import find_version
    version = find_version(session, definition)
    return version.lifecycle_state if version is not None else None


def _enc(n: dict) -> str:
    out = [f"{k}={n[k]}" for k in NODE_KEYS if k in n]
    if "courses" in n:
        out.append("courses: " + ", ".join(n["courses"]))
    if "course_categories" in n:
        out.append("; ".join(f"{k}: {', '.join(v)}" for k, v in n["course_categories"].items()))
    if "eligible_course_query" in n:
        out.append("query " + json.dumps(n["eligible_course_query"], sort_keys=True))
    if "certification_codes" in n:
        out.append("SOC certification per term: " + ", ".join(n["certification_codes"]))
    if "group_caps" in n:
        out.append("; ".join(f"at most {c['max']} of {', '.join(c['courses'])}"
                             for c in n["group_caps"]))
    if n.get("course_list_unknown"):
        out.append("**course list UNKNOWN** (source defers / unarchived)")
    if "unresolved_conflicts" in n:
        out.append("**open conflict** " + ", ".join(n["unresolved_conflicts"]))
    return "<br>".join(out).replace("|", "/")


def to_markdown(p: dict) -> str:
    mc = p["machine_checks"]
    lines = [
        f"# Review packet: `{p['key']}` ({p['kind']}"
        + (f", {p['component_kind']}" if p["component_kind"] else "") + ")",
        "",
        f"- **Lifecycle:** `{p['lifecycle_state']}`",
        f"- **Encoded by:** {p['curated_by']} - encoding is not review",
        f"- **Definition:** `{p['definition_file']}`  semantic sha256 `{p['definition_sha256'][:16]}`",
        "",
        "## Sources",
        "",
        "| id | role | status | retrieved (UTC) | representation | sha256 | url |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in p["sources"]:
        lines.append(f"| `{s['id']}` | {s['role']} | {s['status']}"
                     + ("" if s["archive_present"] or s["status"] != "archived" else " (bytes not local)")
                     + f" | {s['retrieved_at'] or ''} | {s['representation'] or ''} | "
                     f"`{(s['sha256'] or '')[:16]}` | {s['url'] or ''} |")
    for s in p["sources"]:
        if s.get("note"):
            lines.append(f"- `{s['id']}`: {s['note']}")
    lines += ["", "## Machine checks (not review)", "",
              f"- passed: **{mc['passed']}**, quotes found verbatim: "
              f"{mc['quotes_found']}/{mc['quotes_checked']}"]
    lines += [f"- ERROR: {e}" for e in mc["errors"]]
    lines += [f"- warning: {w}" for w in mc["warnings"][:20]]
    if p["conflicts"]:
        lines += ["", "## OPEN conflicts (both claims shown; none resolved)", ""]
        for c in p["conflicts"]:
            lines.append(f"### {c['id']} ({c.get('status', 'open')})")
            if c.get("summary"):
                lines.append(c["summary"])
            lines += [f"- `{q['source']}`: \"{q['quote']}\"" for q in c.get("claims", [])]
            lines.append("")
    lines += ["", "## Review questions", ""]
    lines += [f"- [ ] {q}" for q in p["review_questions"]] or ["- (none recorded)"]
    lines += ["", "## What to verify", ""] + [f"- [ ] {c}" for c in p["what_to_verify"]]
    lines += ["", "## Requirement nodes", "",
              "| node | type | parent | encoding | eligible rows |", "|---|---|---|---|---|"]
    for n in p["requirements"]:
        lines.append(f"| `{n['code']}` {n['name']} | {n['type']} | {n['parent'] or ''} | "
                     f"{_enc(n)} | {n.get('eligible_course_rows', '')} |")
    lines += ["", "### Verbatim quotes per node", ""]
    for n in p["requirements"]:
        via = f" (via `{n['quoted_via']}`)" if n["quoted_via"] else ""
        if not n["quotes"]:
            lines.append(f"- **{n['code']}**: (no quote - structural node)")
            continue
        lines.append(f"- **{n['code']}**{via}:")
        lines += [f"  - `{q['source']}`: \"{q['quote']}\"" for q in n["quotes"]]
        if n.get("notes"):
            lines.append(f"  - note: {n['notes']}")
    if p["school_rules"]:
        lines += ["", "## Rules", ""]
        for r in p["school_rules"]:
            qs = r.get("quotes") or ([{"source": "catalog", "quote": r["source_prose"]}]
                                     if r.get("source_prose") else [])
            lines.append(f"- `{r['code']}` ({r['rule_type']}, evaluable="
                         f"{r.get('is_evaluable', True)}): {r['name']}")
            if not r.get("is_evaluable", True):
                lines.append(f"  - not evaluated: {r.get('not_evaluable_reason')}")
            lines += [f"  - `{q['source']}`: \"{q['quote']}\"" for q in qs]
    lines += ["", "## Rules not yet modeled", ""]
    lines += [f"- {r['rule']}  \n  _why:_ {r['why_not']}"
              + (f"  \n  `{r['source']}`: \"{r['quote']}\"" if r.get("quote") else "")
              for r in p["rules_not_yet_modeled"]] or ["- (none)"]
    lines += ["", "## Unknowns", ""] + ([f"- {u}" for u in p["unknowns"]] or ["- (none)"])
    lines += ["", "## How to record your decision", "", f"```\n{p['how_to_record']}\n```", ""]
    return "\n".join(lines)


def write_packet(path: pathlib.Path, out_dir: pathlib.Path, session=None) -> pathlib.Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    packet = build_packet(path, session)
    (out_dir / f"{path.stem}.review.json").write_text(
        json.dumps(packet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md = out_dir / f"{path.stem}.review.md"
    md.write_text(to_markdown(packet), encoding="utf-8")
    return md


__all__ = ["PACKET_VERSION", "build_packet", "to_markdown", "write_packet"]
