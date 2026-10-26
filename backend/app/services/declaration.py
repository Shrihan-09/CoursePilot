"""Declaring a major: the stated status and the major's published requirements (Phase 7.6).

A student who is officially Undeclared can still plan toward the major they
intend to declare. They say so (`student.declaration_status = 'undeclared'`,
`PUT /api/v1/student/declaration`); their record's program is then the
INTENDED major, and every plan says "planning toward X (not yet declared)".

What it takes to declare is read from a reviewed file per program,
`data/programs/rutgers/nb-undergrad/<year>/declaration/<school>-<code>.json`,
whose every condition carries the verbatim quote and the official page it
came from. Where no such file exists, the plan says that no official source
CoursePilot holds states the requirements - nothing is invented. Where two
official sources disagree, both are shown.

The "C or better in these courses" condition is checked against the
student's own record (met / in progress / below the minimum / not taken);
grades themselves are never repeated back. Statements CoursePilot cannot
check (transfer rules, how to declare) are shown, not evaluated.
"""

from __future__ import annotations

import json
import pathlib
from functools import lru_cache

from sqlalchemy.orm import Session

from app.domain.grades import Tri, meets_minimum
from app.domain.planning import Declaration, DeclarationRequirement, DeclarationSource
from app.models import Student

DEGREE_LABELS = {"BA": "B.A.", "BS": "B.S.", "BFA": "B.F.A.", "BM": "B.Mus."}
STATUSES = ("declared", "undeclared")


def declaration_root() -> pathlib.Path:
    from app.services.degree_audit.mapping import mapping_root

    return mapping_root()


@lru_cache(maxsize=32)
def _load(path: str, mtime: float) -> dict:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def requirements_file(program_key: str, catalog_year: str,
                      root: pathlib.Path | None = None) -> dict | None:
    """The reviewed declaration file that lists `program_key`, or None."""
    folder = (root or declaration_root()) / catalog_year / "declaration"
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.json")):
        data = _load(str(path), path.stat().st_mtime)
        if program_key in data.get("applies_to", []):
            return data
    return None


def program_label(name: str, degree_type: str, variant: str = "") -> str:
    degree = DEGREE_LABELS.get((degree_type or "").upper(), "")
    option = f", {variant.replace('-', ' ').title()}" if variant else ""
    return f"{name}{' ' + degree if degree else ''}{option}"


def _course_status(attempts, minimum: str) -> str:
    if not attempts:
        return "not_taken"
    verdicts = [meets_minimum(a.outcome, minimum) for a in attempts if a.status == "completed"]
    if Tri.YES in verdicts:
        return "met"
    if any(a.status == "in_progress" for a in attempts):
        return "in_progress"
    if Tri.UNKNOWN in verdicts:
        return "unknown"
    return "below_minimum" if verdicts else "not_taken"


def _overall(statuses: list[str]) -> str:
    if any(s in ("not_taken", "below_minimum") for s in statuses):
        return "not_met"
    if "unknown" in statuses:
        return "unknown"
    if "in_progress" in statuses:
        return "in_progress"
    return "met"


def declaration_for(session: Session, student: Student, *, program_key: str,
                    program_name: str, degree_type: str, catalog_year: str,
                    variant: str = "", root: pathlib.Path | None = None) -> Declaration | None:
    """The plan's declaration block. None unless the student stated 'undeclared'."""
    if student.declaration_status != "undeclared":
        return None
    from app.services.prerequisites import attempts_by_course

    label = (f"Planning toward {program_label(program_name, degree_type, variant)} "
             "(not yet declared)")
    data = requirements_file(program_key, catalog_year, root)
    if data is None:
        return Declaration(
            status="undeclared", program_key=program_key, label=label,
            message=("CoursePilot has no official source stating the requirements to declare "
                     "this major, so none are shown or assumed. Ask the department."))
    history = attempts_by_course(session, student)
    requirements = []
    for item in data["requirements"]:
        sources = sorted({q["source"] for q in item.get("quotes", [])})
        if item["kind"] == "courses_min_grade":
            courses = {c: _course_status(history.get(c, []), item["min_grade"])
                       for c in item["courses"]}
            requirements.append(DeclarationRequirement(
                code=item["code"], text=item["text"], sources=sources, courses=courses,
                min_grade=item["min_grade"], status=_overall(list(courses.values()))))
        else:
            requirements.append(DeclarationRequirement(
                code=item["code"], text=item["text"], sources=sources))
    return Declaration(
        status="undeclared", program_key=program_key, label=label, requirements=requirements,
        sources=[DeclarationSource(id=s["id"], title=s["title"], url=s["url"], kind=s["kind"],
                                   retrieved_at=s["retrieved_at"]) for s in data["sources"]],
        conflicts=list(data.get("conflicts", [])))


__all__ = ["STATUSES", "declaration_for", "program_label", "requirements_file"]
