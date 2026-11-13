"""The encoding pipeline, one command per step (docs/ENCODING_MAJORS.md).

    python scripts/encode_program.py archive <id> <url> [<id> <url> ...]
    python scripts/encode_program.py new component|program --code ... --out FILE
    python scripts/encode_program.py validate FILE...
    python scripts/encode_program.py packet FILE... [--with-db]
    python scripts/encode_program.py load FILE...              (writes to the DB)
    python scripts/encode_program.py index rebuild|status|coverage

`load` and `index rebuild` write to the database named by --database-url or
DATABASE_URL_SYNC; the target database NAME (never its credentials) is
printed first. Nothing here marks anything reviewed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from coursepilot_ingestion.encoding.sources import archive_many

YEAR_DIR = pathlib.Path(__file__).resolve().parents[3] / "data" / "programs" / "rutgers" / \
    "nb-undergrad" / "2026-2027"


def _session(url: str | None):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    from coursepilot_ingestion.cli import resolve_database_url

    resolved = resolve_database_url(url)
    print(f"database: {make_url(resolved).database}", file=sys.stderr)
    return Session(create_engine(resolved, future=True))


def _template(kind: str, a) -> dict:
    curation = {"curated_by": "<who encoded it - an AI assistant is recorded here, never as "
                              "reviewer>", "encoded_at": "<YYYY-MM-DD>",
                "curation_status": "curated_from_prose", "extractor_version": "manual-encoding/2"}
    node = {"code": "<ROOT_CODE>", "name": "<name as published>", "requirement_type": "all_of",
            "parent": None, "quotes": [{"source": "<capture id>", "quote": "<verbatim text>"}]}
    common = {"sources": [{"id": "<catalog capture id>", "role": "catalog", "primary": True},
                          {"id": "<department capture id>", "role": "department"}],
              "requirements": [node], "rules_not_yet_modeled": [], "unknowns": [],
              "review_questions": [], "conflicts": []}
    if kind == "component":
        return {"_README": ["<what this component is and why it is shared>"],
                "format": "component/1",
                "component": {"code": a.code, "name": a.name or "<name>",
                              "kind": a.component_kind, "composition": "linked",
                              "school": {"code": a.school, "name": "<school name>",
                                         "campus_code": "NB"},
                              "catalog_year": a.year, "definition_version": "1",
                              "requirement_system": "major", "sharing_policy": "exclusive",
                              "source_url": "<official URL>"},
                **common, "school_rules": [], "curation": curation}
    return {"_README": ["<program, degree, curriculum and its sources>"],
            "source": {"url": "<catalog URL>", "catalog_year": a.year,
                       "retrieved_at": "<YYYY-MM-DD>", "kind": "rutgers_official_catalog",
                       "curation_status": "curated_from_prose"},
            "school": {"code": a.school, "name": "<school name>", "campus_code": "NB"},
            "program": {"code": a.code, "name": a.name or "<name>", "degree_type": "BS"},
            "program_version": {"catalog_year": a.year, "curriculum_key": "",
                                "admission_stage": None, "degree_credits": None},
            "components": [], **common, "program_rules": [],
            "curation": {**curation, "catalog_page": "<catalog path>"}}


def cmd_archive(a) -> int:
    pairs = list(zip(a.pairs[::2], a.pairs[1::2], strict=True))
    for cap in archive_many(pairs):
        print(f"{cap.id}: {cap.representation} {cap.status or ''} "
              f"{(cap.sha256 or '')[:16]} {cap.note or ''}")
    return 0


def cmd_add_capture(a) -> int:
    from coursepilot_ingestion.encoding.sources import add_saved_capture

    rec = add_saved_capture(a.id, a.url, pathlib.Path(a.file), a.captured_at,
                            representation=a.representation, note=a.note)
    print(f"{rec['id']}: {rec['representation']} {rec['retrieved_at']} "
          f"{rec['sha256'][:16]} -> {rec['archive_path']}")
    return 0


def cmd_new(a) -> int:
    out = pathlib.Path(a.out)
    if out.exists():
        print(f"refused: {out} exists", file=sys.stderr)
        return 2
    out.write_text(json.dumps(_template(a.kind, a), indent=2) + "\n", encoding="utf-8")
    print(f"wrote template {out} - fill every <...>; nothing in it is a requirement yet")
    return 0


def cmd_validate(a) -> int:
    from coursepilot_ingestion.encoding.validate import validate_file

    session = _session(a.database_url) if a.with_db else None
    bad = 0
    for f in a.files:
        r = validate_file(pathlib.Path(f), session)
        bad += not r.ok
        print(f"{'OK ' if r.ok else 'BAD'} {f}: quotes {r.quotes_found}/{r.quotes_checked}")
        for e in r.errors:
            print(f"    ERROR {e}")
        for w in r.warnings:
            if "structural node" not in w:
                print(f"    warning {w}")
    return 1 if bad else 0


def cmd_packet(a) -> int:
    from coursepilot_ingestion.encoding.packets import write_packet

    session = _session(a.database_url) if a.with_db else None
    for f in a.files:
        path = pathlib.Path(f)
        out = path.parent / "review"
        print(write_packet(path, out, session))
    return 0


def cmd_load(a) -> int:
    from coursepilot_ingestion.encoding.validate import file_kind
    from coursepilot_ingestion.loaders.components import ComponentLoader
    from coursepilot_ingestion.loaders.requirements import RequirementLoader

    with _session(a.database_url) as s:
        for f in a.files:
            path = pathlib.Path(f)
            definition = json.loads(path.read_bytes())
            if file_kind(definition) == "transitions":
                from coursepilot_ingestion.loaders.transitions import load_transitions
                st = load_transitions(s, path)
                print(f"{f}: transitions {st}")
            elif file_kind(definition) == "component":
                st = ComponentLoader(s).load_file(path)
                print(f"{f}: component {st.code} inserted={st.inserted} "
                      f"changed={st.definition_changed} nodes+{st.requirements_inserted}"
                      f"/~{st.requirements_updated}/-{st.requirements_removed} "
                      f"eligibility+{st.eligibility_inserted}/-{st.eligibility_removed} "
                      f"unresolved={len(st.unresolved_courses)}")
            else:
                st = RequirementLoader(s).load_file(path)
                print(f"{f}: {st.summary()} changed={st.definition_changed}")
        s.commit()
    return 0


def cmd_index(a) -> int:
    from app.services import knowledge

    with _session(a.database_url) as s:
        if a.action == "rebuild":
            build = knowledge.ensure_fresh(s) if not a.force else knowledge.build_index(s)
            s.commit()
            print(json.dumps({"build": str(build.id), "state": build.state,
                              "counts": build.counts}, indent=1))
        elif a.action == "status":
            build = knowledge.current_build(s)
            fresh = knowledge.is_fresh(s, build) if build else False
            print(json.dumps({"build": str(build.id) if build else None,
                              "fresh": fresh, "counts": build.counts if build else None,
                              "completed_at": str(build.completed_at) if build else None},
                             indent=1))
        else:
            rows = knowledge.coverage_report(s)
            terms = sorted({r["term"] for r in rows})
            want = a.term or (terms[-1] if terms else None)
            for r in rows:
                if r["term"] != want:
                    continue
                print(f"{r['program']:28} {r['term']} offered={r['offered']:5} "
                      f"mapped={r['mapped']:5} excluded={r['excluded']:3} "
                      f"complete={r['complete']}")
                for reason in r["reasons"][:6]:
                    print(f"{'':30}- {reason}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="encode_program")
    p.add_argument("--database-url", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    ar = sub.add_parser("archive", help="archive official sources (plain HTTP)")
    ar.add_argument("pairs", nargs="+", help="<capture id> <url> pairs")
    ac = sub.add_parser("add-capture", help="record a page saved from a browser")
    ac.add_argument("id")
    ac.add_argument("url")
    ac.add_argument("file")
    ac.add_argument("--captured-at", required=True, help="ISO date/time the page was saved")
    ac.add_argument("--representation", default="browser_saved")
    ac.add_argument("--note")
    nw = sub.add_parser("new", help="write an empty encoding template")
    nw.add_argument("kind", choices=("component", "program"))
    nw.add_argument("--code", required=True)
    nw.add_argument("--school", required=True)
    nw.add_argument("--name")
    nw.add_argument("--year", default="2026-2027")
    nw.add_argument("--component-kind", default="school_core",
                    choices=("gen_ed", "school_core", "entry_stage", "shared_major"))
    nw.add_argument("--out", required=True)
    for name in ("validate", "packet"):
        x = sub.add_parser(name)
        x.add_argument("files", nargs="+")
        x.add_argument("--with-db", action="store_true",
                       help="also check courses / count eligibility in the database")
    ld = sub.add_parser("load", help="load encodings into the database (writes)")
    ld.add_argument("files", nargs="+")
    ix = sub.add_parser("index", help="the course knowledge index")
    ix.add_argument("action", choices=("rebuild", "status", "coverage"))
    ix.add_argument("--force", action="store_true")
    ix.add_argument("--term")
    return p


COMMANDS = {"archive": cmd_archive, "add-capture": cmd_add_capture, "new": cmd_new, "validate": cmd_validate,
            "packet": cmd_packet, "load": cmd_load, "index": cmd_index}


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return COMMANDS[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
