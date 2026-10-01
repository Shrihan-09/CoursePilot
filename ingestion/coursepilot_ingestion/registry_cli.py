"""Program registry command line (Phase 6.3).

    python -m coursepilot_ingestion.registry_cli discover --year 2026-2027
    python -m coursepilot_ingestion.registry_cli fetch --year 2026-2027 --school sas --subject 220 [--network]
    python -m coursepilot_ingestion.registry_cli load            # every curated definition
    python -m coursepilot_ingestion.registry_cli link            # provenance + source check
    python -m coursepilot_ingestion.registry_cli validate        # deterministic checks -> validated
    python -m coursepilot_ingestion.registry_cli check-sources   # downgrade on source change
    python -m coursepilot_ingestion.registry_cli status
    python -m coursepilot_ingestion.registry_cli packets         # review packets (JSON + markdown)
    python -m coursepilot_ingestion.registry_cli review  --program sas-640-ba-option-a --year 2026-2027 --reviewer "Full Name" --decision approved
    python -m coursepilot_ingestion.registry_cli publish --program sas-640-ba-option-a --year 2026-2027

`review` and `publish` are for the HUMAN reviewer. They refuse machine and
placeholder names (app.services.program_lifecycle). Running them on a
person's behalf would be exactly the fabricated review the lifecycle exists
to prevent.

The network is used only by `fetch --network`, only for public catalog
pages listed in Rutgers' own navigation, one at a time with a pause.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from app.models import CatalogPage, Program, ProgramCandidate, ProgramVersion, School
from app.services import program_lifecycle
from app.services.programs import find_program
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from coursepilot_ingestion.cli import resolve_database_url
from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.review_packets import write_packets
from coursepilot_ingestion.program_registry import (
    ARCHIVE_DIR,
    archive_name,
    check_sources,
    curated_files,
    discover_year,
    fetch_page,
    find_version,
    link_definition,
    validate_and_mark,
)

#: Any archived page of a year carries the whole navigation tree.
NAV_SEED = "computer-science-198"


def _seed_html(year: str) -> str:
    path = ARCHIVE_DIR / f"catalog_{NAV_SEED}_{year.replace('-', '_')}.html"
    return path.read_text(encoding="utf-8")


def _page_html(definition: dict) -> str | None:
    path = ARCHIVE_DIR / archive_name(definition["program_version"]["catalog_year"],
                                      definition["curation"]["catalog_page"])
    return path.read_text(encoding="utf-8") if path.exists() else None


def cmd_discover(s: Session, a) -> None:
    st = discover_year(s, a.year, _seed_html(a.year))
    print(f"{a.year}: seen={st.pages_seen} inserted={st.pages_inserted} updated={st.pages_updated}")


def cmd_fetch(s: Session, a) -> None:
    q = select(CatalogPage).where(CatalogPage.catalog_year == a.year,
                                  CatalogPage.page_class == "subject_coded")
    if a.school:
        q = q.where(CatalogPage.school_slug == a.school)
    if a.subject:
        q = q.where(CatalogPage.subject_code.in_(a.subject))
    pages = list(s.scalars(q.order_by(CatalogPage.url_path)))
    if len(pages) > a.max_pages:
        raise SystemExit(f"{len(pages)} pages selected; --max-pages is {a.max_pages}. "
                         "Fetching is deliberately small; raise the limit explicitly.")
    for page in pages:
        try:
            _, st = fetch_page(s, page, allow_network=a.network)
        except FileNotFoundError as exc:
            print(f"  skip {page.url_path}: {exc}")
            continue
        print(f"  {page.url_path}: prose {page.prose_sha256[:16]} candidates "
              f"+{st.candidates_inserted} ={st.candidates_unchanged} retired={st.candidates_retired}")


def cmd_load(s: Session, a) -> None:
    for f in curated_files():
        st = RequirementLoader(s).load_file(f)
        print(f"  {f.name}: {st.summary()} changed={st.definition_changed}")


def _definitions():
    for f in curated_files():
        yield f, json.loads(f.read_bytes())


def cmd_link(s: Session, a) -> None:
    for f, d in _definitions():
        v = find_version(s, d)
        if v is None:
            print(f"  {f.name}: not loaded")
            continue
        r = link_definition(s, d, v)
        print(f"  {f.name}: page={r.page_found} fetched={r.fetched} "
              f"candidate={r.candidate_linked} source={r.source_check} -> {v.lifecycle_state}")


def cmd_validate(s: Session, a) -> None:
    for f, d in _definitions():
        r = validate_and_mark(s, d, _page_html(d))
        v = find_version(s, d)
        print(f"  {f.name}: ok={r.ok} quotes {r.quotes_found}/{r.quotes_checked} "
              f"-> {v.lifecycle_state if v else 'not loaded'}")
        for e in r.errors:
            print(f"      ERROR {e}")
        for w in r.warnings[:5]:
            print(f"      warn  {w}")


def cmd_check(s: Session, a) -> None:
    for k, v in check_sources(s).items():
        print(f"  {k}: {v}")


def cmd_status(s: Session, a) -> None:
    pages = list(s.scalars(select(CatalogPage)))
    print("catalog pages:", dict(Counter((p.catalog_year, p.lifecycle) for p in pages)))
    cands = list(s.scalars(select(ProgramCandidate)))
    print("candidates:", dict(Counter(c.credential_type for c in cands)),
          f"linked={sum(1 for c in cands if c.program_version_id)}")
    for v, p, sch in s.execute(select(ProgramVersion, Program, School)
                               .join(Program, Program.id == ProgramVersion.program_id)
                               .join(School, School.id == Program.school_id)
                               .order_by(Program.code, Program.variant)):
        print(f"  {sch.code} {p.code} {p.degree_type} {p.variant or '-':10} {v.catalog_year} "
              f"{v.lifecycle_state:15} basis={v.publication_basis} curated_by={v.curated_by!r}")


def cmd_packets(s: Session, a) -> None:
    files = curated_files()
    out = files[0].parent / "review"
    for path in write_packets(files, out, s):
        print(f"  {path}")
    s.rollback()                     # packets read; they never change state


def _version_for(s: Session, a) -> ProgramVersion:
    found = find_program(s, a.program)
    if found is None:
        raise SystemExit(f"no program {a.program}")
    v = next((v for v in found[0].versions if v.catalog_year == a.year), None)
    if v is None:
        raise SystemExit(f"{a.program} has no {a.year} version")
    return v


def cmd_review(s: Session, a) -> None:
    v = _version_for(s, a)
    review = program_lifecycle.record_review(s, v, reviewer=a.reviewer, decision=a.decision,
                                             notes=a.notes)
    print(f"recorded {review.decision} by {review.reviewer} -> {v.lifecycle_state}")


def cmd_publish(s: Session, a) -> None:
    v = _version_for(s, a)
    program_lifecycle.publish(s, v)
    print(f"published ({v.publication_basis})")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coursepilot-registry")
    p.add_argument("--database-url", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--year", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--year", required=True)
    f.add_argument("--school")
    f.add_argument("--subject", nargs="*")
    f.add_argument("--network", action="store_true", help="allow live public-catalog fetches")
    f.add_argument("--max-pages", type=int, default=12)
    for name in ("load", "link", "validate", "check-sources", "status", "packets"):
        sub.add_parser(name)
    for name in ("review", "publish"):
        r = sub.add_parser(name)
        r.add_argument("--program", required=True)
        r.add_argument("--year", required=True)
        if name == "review":
            r.add_argument("--reviewer", required=True, help="your full name")
            r.add_argument("--decision", choices=("approved", "changes_requested"), required=True)
            r.add_argument("--notes")
    return p


COMMANDS = {"discover": cmd_discover, "fetch": cmd_fetch, "load": cmd_load, "link": cmd_link,
            "validate": cmd_validate, "check-sources": cmd_check, "status": cmd_status,
            "packets": cmd_packets, "review": cmd_review, "publish": cmd_publish}


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    engine = create_engine(resolve_database_url(a.database_url), future=True)
    with Session(engine) as s:
        try:
            COMMANDS[a.cmd](s, a)
        except program_lifecycle.LifecycleError as exc:
            s.rollback()
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        s.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
