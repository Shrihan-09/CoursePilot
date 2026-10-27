"""Read-only, public catalog evidence for the science-sequence investigation."""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.session import get_sync_sessionmaker  # noqa: E402
from app.domain.attempts import Attempt  # noqa: E402
from app.domain.corequisites import parse_note  # noqa: E402
from app.domain.prerequisites import AllOf, AnyOf, CourseReq, from_json, to_text  # noqa: E402
from app.models import (  # noqa: E402
    Course,
    CourseCorequisite,
    CourseOffering,
    CoursePrerequisite,
    Student,
)
from app.services.course_eligibility import check_proposal  # noqa: E402
from sqlalchemy import select  # noqa: E402

CODES = [f"01:160:{n}" for n in (159, 160, 161, 162, 163, 164, 171)] + [
    f"01:750:{n}" for n in (193, 194, 203, 204, 205, 206)]


def main():
    with get_sync_sessionmaker()() as session:
        offerings = session.execute(select(Course.course_string, CourseOffering.term_code).join(
            CourseOffering, CourseOffering.course_id == Course.id).where(
            Course.course_string.in_(CODES), Course.supplement_code == "",
            CourseOffering.term_code >= "20261")).all()
        loaded = set(offerings)
        student = Student(catalog_year="2026-2027")
        # Explicit diagnostic scenario: mathematics through Calc I completed;
        # science prerequisites offered as same-term partners, never passed.
        projected = [Attempt("01:640:151", "20259", "completed", "B")]
        for term in sorted({term for _, term in loaded}):
            for code in CODES:
                print(json.dumps(dict(course=code, term=term, model="availability",
                                      loaded_offering=(code, term) in loaded)))
            path = ROOT / "data" / "raw" / f"soc_courses_{term[:4]}_{term[4]}_NB.json"
            if not path.exists():
                continue
            for raw in json.loads(path.read_bytes()):
                code = raw.get("courseString")
                if (code, term) not in loaded or (raw.get("supplementCode") or "").strip():
                    continue
                print(json.dumps(dict(course=code, term=term, model="raw_SOC",
                    preReqNotes=raw.get("preReqNotes"), courseNotes=raw.get("courseNotes"),
                    sectionNotes=[dict(section=s.get("number"), notes=s.get("sectionNotes"))
                                  for s in raw.get("sections", [])])))
            keys = CODES
            # A detached student has no rows; projected history is explicit.
            import uuid
            student.id = uuid.uuid4()
            scenarios = [(name, history, proposed)
                         for name, history in (
                             ("math_only", projected),
                             ("science_prerequisites_completed", projected + [
                                 Attempt(k, "20259", "completed", "B") for k in CODES]))
                         for proposed in (frozenset(), frozenset(CODES))]
            for scenario, history, proposed in scenarios:
                for code, check in check_proposal(session, student, keys, term,
                        proposed, projected=history, independent=True).items():
                    print(json.dumps(dict(course=code, term=term, model="eligibility",
                        scenario=scenario,
                        proposed=sorted(proposed), status=check.status.value,
                        prerequisite=check.prerequisite.status.value,
                        corequisite=check.corequisite.status.value,
                        failures=check.prerequisite.reasons + check.corequisite.reasons)))
        for model in (CoursePrerequisite, CourseCorequisite):
            for code, row in session.execute(select(Course.course_string, model).join(
                    Course, Course.id == model.course_id).where(
                    Course.course_string.in_(CODES), model.term_code >= "20261").order_by(
                    Course.course_string, model.term_code)):
                fields = {k: getattr(row, k, None) for k in (
                    "term_code", "raw_text", "condition_note", "section_condition_note",
                    "source_field", "classification", "expression", "canonical_text",
                    "parse_detail", "interpreted_conditions")}
                if model is CoursePrerequisite and row.expression:
                    expression = from_json(row.expression)
                    lists = (row.interpreted_conditions or {}).get("prerequisite_lists", ())
                    if lists:
                        expression = AllOf((expression, *(AnyOf(tuple(CourseReq(k) for k in codes))
                                                          for codes in lists)))
                    fields["effective_canonical"] = to_text(expression)
                if model is CourseCorequisite:
                    fields["current_parser"] = [dict(classification=p.classification,
                        canonical=p.canonical_text, detail=p.detail)
                        for note in row.raw_text.split(" || ")
                        for p in parse_note(note, lambda subject, number:
                            session.scalar(select(Course.course_string).where(
                                Course.subject_code == subject, Course.course_number == number,
                                Course.supplement_code == "")))]
                print(json.dumps(dict(course=code, model=model.__name__, **fields)))
        session.rollback()


if __name__ == "__main__":
    main()
