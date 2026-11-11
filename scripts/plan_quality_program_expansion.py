"""Plan-quality numbers for Prompt 08 B1's synthetic students (one per school).

Builds the same in-memory world as ingestion/tests/test_program_expansion.py
(real public SOC records + the real school components + SYNTHETIC programs
and FABRICATED students), freezes the clock in October 2026 and plans from
Spring 2027. The SAS student is the existing CS B.S. scenario world.

Prints, per student: Spring 2027 courses/credits, courses labelled "after
<stage>", requirement leaves met before/after the plan, leaves that stay open
because their course list is unknown, graduation term, and plan issues.
No database server is touched.

Usage:  python scripts/plan_quality_program_expansion.py
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "ingestion"), str(ROOT / "backend")]
for k in ("DATABASE_URL", "DATABASE_URL_SYNC", "TEST_DATABASE_URL"):
    os.environ.pop(k, None)
os.environ["ACADEMIC_TODAY"] = "2026-10-15"

from app.db.base import Base
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

SPRING27 = "20271"


def _session():
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    import app.models  # noqa: F401
    Base.metadata.create_all(engine)
    return Session(engine)


def _load_terms(session, fixture: pathlib.Path, tmp: pathlib.Path):
    from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
    from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
    from coursepilot_ingestion.sources.soc import SocQuery

    combined = {}
    for term, records in json.loads(fixture.read_bytes()).items():
        year, season = term.split("_")
        folder = tmp / term
        folder.mkdir()
        (folder / f"soc_courses_{year}_{season}_NB.json").write_text(json.dumps(records), "utf-8")
        query = SocQuery(year=int(year), term=season, campus="NB")
        CourseIngestionPipeline(session, folder).run(query, coverage="complete")
        SectionIngestionPipeline(session, folder).run(query)
        combined.update({r["courseString"]: r for r in records})
    return combined


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.children)


def _metrics(session, student, version, label):
    from app.domain.planning import (
        FillerPreference,
        Pace,
        PlanningConstraints,
        WorkloadLevel,
    )
    from app.services.audit.engine import DegreeAuditEngine
    from app.services.planning.service import generate_plan

    before = DegreeAuditEngine(session).audit(student, program_version=version)
    plan = generate_plan(session, student, start_term=SPRING27, constraints=PlanningConstraints(
        workload_level=WorkloadLevel.SEMI_RIGOROUS, filler_preference=FillerPreference.FLEXIBLE,
        max_courses_per_term=6, pace=Pace.BALANCED))
    spring = next((t for t in plan.terms if t.term_code == SPRING27), None)
    courses = [pc for t in plan.terms for pc in t.courses]
    leaves = [n for n in _walk(before.requirements) if not n.children]
    met = {"satisfied", "provisionally_satisfied"}
    unknown = sorted({r.code for r in _composed(session, version)
                      if r.requirement_type in ("course", "choose_n") and r.eligibility_rule == {}
                      and not r.certification_codes})
    after_met = sum(1 for r in plan.requirements if r.status_after in met)
    return {
        "student": label,
        "spring_2027_courses": [pc.course for pc in spring.courses] if spring else [],
        "spring_2027_credits": str((spring.credits + spring.free_elective_credits)
                                   if spring else 0),
        "courses_planned_total": len(courses),
        "labelled_after_stage": sorted({f"{pc.course} ({pc.after_stage_label})"
                                        for pc in courses if pc.after_stage_label}),
        "leaves_met_before": sum(1 for n in leaves if n.status.value in met),
        "leaves_total": len(leaves),
        "requirements_met_after_plan": after_met,
        "requirements_total": len(plan.requirements),
        "open_because_course_list_unknown": unknown,
        "graduation_target": plan.graduation_target.term_code if plan.graduation_target else None,
        "last_planned_term": plan.terms[-1].term_code if plan.terms else None,
        "plan_status": str(getattr(plan.status, "value", plan.status)),
        "degree_total": str(plan.degree_credits.required) if plan.degree_credits
        and hasattr(plan.degree_credits, "required") else None,
        "issues": sorted({i.code for i in plan.issues}),
    }


def _composed(session, version):
    from app.services.academic_context import composed_requirements
    return composed_requirements(session, version)


def main() -> None:
    sys.path.insert(0, str(ROOT))
    from ingestion.tests import test_program_expansion as t
    from ingestion.tests import test_workload_levels as w

    out = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        # SAS: the existing CS B.S. world (real CS B.S. + SAS Core).
        s = _session()
        (tmp / "sas").mkdir()
        from app.models import ProgramVersion

        from coursepilot_ingestion.loaders.requirements import RequirementLoader
        from coursepilot_ingestion.pipelines.core import CoreIngestionPipeline
        combined = _load_terms(s, t.FIXTURE_DIR / "soc_workload_terms_sample.json", tmp / "sas")
        RequirementLoader(s).load_file(w.CS_BS)
        s.commit()
        archive = tmp / "combined.json"
        archive.write_text(json.dumps(list(combined.values())), "utf-8")
        CoreIngestionPipeline(s).run(t.CURATED_DIR / "sas-core.json", archive,
                                     observed_term_code=SPRING27)
        s.commit()
        student = w._student(s)
        out.append(_metrics(s, student, s.get(ProgramVersion, student.program_version_id),
                            "SAS CS B.S. (real encoding)"))
        s.close()

        # RBS, SoE, SEBS: real components + SYNTHETIC programs.
        s = _session()
        (tmp / "b1").mkdir()
        _load_terms(s, t.FIXTURE_DIR / "soc_program_expansion_terms.json", tmp / "b1")
        from coursepilot_ingestion.loaders.components import ComponentLoader
        for code in t.LOAD_ORDER:
            ComponentLoader(s).load_file(t.COMPONENTS / f"{code}.json")
        s.commit()
        progs = {k: t._load_program(s, copy.deepcopy(d)) for k, d in
                 (("rbs", t.RBS_MAJOR), ("soe", t.SOE_MAJOR), ("sebs", t.SEBS_MAJOR))}
        s.commit()
        cases = [
            ("RBS pre-business (synthetic major)", "rbs", [("33:010:272", "20269", "A"),
                                                           ("01:220:102", "20269", "A")]),
            ("SoE first-year (synthetic major)", "soe", [("01:640:151", "20269", "B"),
                                                         ("01:750:123", "20269", "B"),
                                                         ("14:440:101", "20269", "A"),
                                                         ("01:355:101", "20269", "B")]),
            ("SEBS first-year (synthetic major)", "sebs", [("01:119:115", "20269", "B")]),
        ]
        for label, key, history in cases:
            student = t._student(s, progs[key], history, ref=f"pq-{key}")
            out.append(_metrics(s, student, progs[key], label))
        s.close()
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
