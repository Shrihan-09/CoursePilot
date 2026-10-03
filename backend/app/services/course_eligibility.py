"""Course-taking eligibility for a proposed term (Phase 6.4).

    "May this student take course X in term T, alongside courses P?"

The primitive a future Planning Engine will call. It does NOT choose X, T or
P, rank anything or build a plan - it evaluates one proposal, deterministically,
and returns evidence.

```
prerequisite (app.services.prerequisites)  -- grades, alternatives, caps -->  P
co-requisite (course_corequisite)          -- completed + proposed term -->   C
eligibility = P AND C
            = P OR C   when the co-requisite is "PRE OR COREQ: X" and X is
                       exactly the prerequisite's own course(s): the
                       department relaxed the prerequisite to allow taking
                       X in the same term
```

Three-valued throughout (Kleene): one UNSATISFIED decides an AND, one
SATISFIED decides an OR, otherwise UNKNOWN wins over SATISFIED. A
co-requisite that is published but not interpreted is UNKNOWN - never ignored
and never assumed met.

Degree requirements are NOT consulted here: whether a course COUNTS toward a
degree is the Degree Engine's question.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.prerequisites import (
    ConcurrentReq,
    Evaluation,
    GradeCondition,
    PrereqStatus,
    _combine_all,
    _combine_any,
    evaluate,
    from_json,
)
from app.models import Course, CourseCorequisite, Student
from app.services.prerequisites import PrerequisiteCheck, attempts_by_course, check_many


@dataclass(slots=True)
class CorequisiteCheck:
    has_corequisite: bool
    status: PrereqStatus
    raw_text: str | None = None
    source_field: str | None = None
    classification: str | None = None
    canonical_text: str | None = None
    evidence: Evaluation | None = None
    relaxes_prerequisite: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EligibilityCheck:
    course_key: str
    term_code: str
    proposed_with: tuple[str, ...]
    status: PrereqStatus
    combination: str                # "prerequisite" | "prerequisite AND corequisite" | "... OR ..."
    prerequisite: PrerequisiteCheck
    corequisite: CorequisiteCheck


def _grade_condition(pre: PrerequisiteCheck) -> GradeCondition | None:
    minimum = (pre.interpreted_conditions or {}).get("minimum_grade")
    if not minimum:
        return None
    return GradeCondition(minimum["grade"], minimum["scope"], frozenset(minimum.get("courses", ())))


def _corequisite(rows: list[CourseCorequisite], history, term_code: str,
                 proposed: frozenset[str], prerequisite_courses: set[str],
                 grade_condition: GradeCondition | None = None) -> CorequisiteCheck:
    if not rows:
        return CorequisiteCheck(False, PrereqStatus.SATISFIED, reasons=["no_corequisite_published"])
    if len({(r.raw_text, r.canonical_text) for r in rows}) > 1:
        return CorequisiteCheck(True, PrereqStatus.UNKNOWN, raw_text=rows[0].raw_text,
                                reasons=["campus_corequisites_differ"])
    row = rows[0]
    if row.expression is None:
        return CorequisiteCheck(True, PrereqStatus.UNKNOWN, raw_text=row.raw_text,
                                source_field=row.source_field, classification=row.classification,
                                reasons=[f"corequisite_not_interpreted:{row.parse_detail or ''}"])
    expr = from_json(row.expression)
    evidence = evaluate(expr, history, term_code=term_code, proposed=proposed,
                        grade_condition=grade_condition)
    leaves = []

    def walk(e):
        if isinstance(e, ConcurrentReq):
            leaves.append(e)
        for child in getattr(e, "children", ()):
            walk(child)

    walk(expr)
    relaxes = bool(leaves) and all(leaf.prior_allowed is True for leaf in leaves) and (
        {leaf.course_key for leaf in leaves} == prerequisite_courses)
    return CorequisiteCheck(True, evidence.status, raw_text=row.raw_text,
                            source_field=row.source_field, classification=row.classification,
                            canonical_text=row.canonical_text, evidence=evidence,
                            relaxes_prerequisite=relaxes, reasons=list(evidence.unknown_reasons))


def check_proposal(session: Session, student: Student, course_keys_: list[str], term_code: str,
                   proposed: frozenset[str] | set[str] = frozenset()) -> dict[str, EligibilityCheck]:
    """Eligibility of each candidate course for `term_code`, given the term's
    other proposed courses. Fixed query count whatever the candidate count.

    A candidate is never its own co-requisite partner: X in `proposed` does
    not satisfy a co-requisite naming X for X itself.
    """
    proposed = frozenset(proposed) | frozenset(course_keys_)
    prerequisites = check_many(session, student, course_keys_, term_code)
    history = attempts_by_course(session, student)
    coreq_rows: dict[str, list[CourseCorequisite]] = {}
    for key, row in session.execute(
        select(Course.course_string, CourseCorequisite)
        .join(Course, Course.id == CourseCorequisite.course_id)
        .where(Course.course_string.in_(course_keys_), CourseCorequisite.term_code == term_code)
    ).all():
        coreq_rows.setdefault(key, []).append(row)

    out: dict[str, EligibilityCheck] = {}
    for key in course_keys_:
        pre = prerequisites[key]
        co = _corequisite(coreq_rows.get(key, []), history, term_code, proposed - {key},
                          set(pre.expression_courses), _grade_condition(pre))
        if not co.has_corequisite:
            status, how = pre.status, "prerequisite"
        else:
            # Combine the prerequisite's OWN verdict, then re-apply any
            # uninterpreted condition: "NOT OPEN TO NATIVE SPEAKERS" restricts
            # the course however its co-requisite is met.
            core = (pre.evidence.uncapped_status
                    if pre.evidence is not None and pre.evidence.uncapped_status else pre.status)
            if co.relaxes_prerequisite:
                status, how = _combine_any([core, co.status]), "prerequisite OR corequisite"
            else:
                status, how = _combine_all([core, co.status]), "prerequisite AND corequisite"
            if (status is PrereqStatus.SATISFIED and pre.evidence is not None
                    and pre.evidence.unmodeled_conditions):
                status = PrereqStatus.UNKNOWN
        out[key] = EligibilityCheck(key, term_code, tuple(sorted(proposed - {key})), status,
                                    how, pre, co)
    return out


__all__ = ["CorequisiteCheck", "EligibilityCheck", "check_proposal"]
