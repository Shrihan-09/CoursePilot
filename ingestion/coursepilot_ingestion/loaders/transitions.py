"""Load department-announced course transitions (Prompt 08 B1 follow-up).

Input: data/programs/rutgers/nb-undergrad/course-transitions.json. Each entry
quotes its sources (validated like any encoding: encoding/validate.py).

An ANNOUNCED course that SOC has never listed (01:198:122 before Fall 2027)
gets a `course` row so it can be planned - with a `data_source` of kind
"department_announcement", the announced title and credits, level U, and no
offerings. It is never a fabricated SOC record: SOC data, once loaded,
updates the same natural key.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from datetime import UTC, datetime
from decimal import Decimal

from app.models import Course, CourseTransition, DataSource, Subject
from sqlalchemy import select
from sqlalchemy.orm import Session

KINDS = {"retired", "announced"}


def validate_transitions(definition: dict) -> list[str]:
    errors = []
    seen = set()
    for t in definition.get("transitions", []):
        key = (t.get("course"), t.get("kind"))
        if key in seen:
            errors.append(f"duplicate transition {key}")
        seen.add(key)
        if t.get("kind") not in KINDS:
            errors.append(f"{t.get('course')}: unknown kind {t.get('kind')!r}")
        if not (t.get("term_code") or "").isdigit() or len(t["term_code"]) != 5:
            errors.append(f"{t.get('course')}: term_code must be a Rutgers term (YYYYT)")
        if not t.get("quotes"):
            errors.append(f"{t.get('course')}: a transition must quote its source")
        if t.get("kind") == "announced" and (t.get("title") is None or t.get("credits") is None):
            errors.append(f"{t.get('course')}: an announced course needs its announced title "
                          "and credits")
        for s in t.get("seasons") or ():
            if s not in ("0", "1", "7", "9"):
                errors.append(f"{t.get('course')}: unknown season {s!r}")
    return errors


def load_transitions(session: Session, path: pathlib.Path) -> dict:
    raw = path.read_bytes()
    definition = json.loads(raw)
    problems = validate_transitions(definition)
    if problems:
        raise ValueError("; ".join(problems))
    digest = hashlib.sha256(raw).hexdigest()
    source = session.scalar(select(DataSource).where(DataSource.content_hash == digest))
    if source is None:
        source = DataSource(kind="department_announcement", url=str(path.name),
                            title="Department-announced course transitions",
                            retrieved_at=datetime.now(UTC), content_hash=digest)
        session.add(source)
        session.flush()
    stats = {"transitions": 0, "courses_created": 0, "removed": 0}
    wanted = set()
    for t in definition["transitions"]:
        wanted.add((t["course"], t["kind"]))
        ref = "; ".join(f"{q['source']}: \"{q['quote']}\"" for q in t["quotes"])
        row = session.scalar(select(CourseTransition).where(
            CourseTransition.course_string == t["course"], CourseTransition.kind == t["kind"]))
        values = dict(term_code=t["term_code"], seasons=t.get("seasons"), title=t.get("title"),
                      credits=Decimal(str(t["credits"])) if t.get("credits") is not None else None,
                      satisfies_prerequisite_of=t.get("satisfies_prerequisite_of"),
                      assumed_prerequisite=t.get("assumed_prerequisite"),
                      planner_note=t.get("planner_note"), note_programs=t.get("note_programs"),
                      source_reference=ref, needs_review=t.get("needs_review"))
        if row is None:
            session.add(CourseTransition(course_string=t["course"], kind=t["kind"], **values))
        else:
            for k, v in values.items():
                setattr(row, k, v)
        stats["transitions"] += 1
        if t["kind"] == "announced":
            stats["courses_created"] += _ensure_course(session, t, source)
    for row in list(session.scalars(select(CourseTransition))):
        if (row.course_string, row.kind) not in wanted:
            session.delete(row)
            stats["removed"] += 1
    session.flush()
    return stats


def _ensure_course(session: Session, t: dict, source: DataSource) -> int:
    unit, subject_code, number = t["course"].split(":")
    exists = session.scalar(select(Course.id).where(
        Course.offering_unit_code == unit, Course.subject_code == subject_code,
        Course.course_number == number, Course.supplement_code == ""))
    if exists is not None:
        return 0
    subject = session.scalar(select(Subject).where(Subject.code == subject_code))
    if subject is None:
        raise ValueError(f"{t['course']}: subject {subject_code} is not loaded; load SOC first")
    session.add(Course(offering_unit_code=unit, subject_code=subject_code, course_number=number,
                       supplement_code="", course_string=t["course"], title=t["title"].upper(),
                       credits=Decimal(str(t["credits"])), level="U", subject_id=subject.id,
                       source_id=source.id,
                       credits_description="announced by the department (not yet in SOC)"))
    session.flush()
    return 1


__all__ = ["load_transitions", "validate_transitions"]
