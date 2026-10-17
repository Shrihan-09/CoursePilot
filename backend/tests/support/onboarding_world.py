"""A SYNTHETIC catalog for Phase 7.0 onboarding tests (real PostgreSQL).

Course codes must look like Rutgers codes (`SS:DDD:CCC`) because the history
API and the transcript normalizer accept nothing else. Offering unit `98`
does not exist at Rutgers, and each world takes a fresh three-digit subject
under it, so these courses can never collide with real catalog data or with
another run.

```
school OB ── PUB   (published)       2026-2027: ROOT{REQ_A: A, REQ_B: B} + CORE_D (core): D
         ├── PEND  (validated)       2026-2027: REQ_C: C           (no core nodes)
         ├── EMPTY (no requirements) 2026-2027                     -> unavailable
         └── OLD   (published)       2025-2026 only
courses A..F under 98:<subject>:101..106; F exists ONLY as an "LB" supplement record.
```
"""

from __future__ import annotations

import datetime as dt
import decimal
import random
import uuid

from sqlalchemy import select

from app.models import (
    Course,
    CurationStatus,
    DataSource,
    Program,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    School,
    Subject,
    UserAccount,
)

CURATED = CurationStatus.CURATED_FROM_PROSE.value


def _subject(session, source) -> str:
    while True:
        code = f"{random.randint(100, 999)}"
        if session.scalar(select(Subject).where(Subject.offering_unit_code == "98",
                                                Subject.code == code)) is None:
            session.add(Subject(code=code, offering_unit_code="98", description="OB",
                                source_id=source.id))
            session.flush()
            return code


def build(session) -> dict:
    s = uuid.uuid4().hex[:6]
    source = DataSource(kind="manual_curation", url=f"synthetic://ob/{s}", content_hash=s,
                        retrieved_at=dt.datetime.now(dt.UTC), version=1)
    session.add(source)
    session.flush()
    subject = _subject(session, source)
    subject_id = session.scalar(select(Subject.id).where(Subject.offering_unit_code == "98",
                                                         Subject.code == subject))
    courses = {}
    for i, name in enumerate("ABCDEF"):
        number = f"{101 + i}"
        supplement = "LB" if name == "F" else ""
        course = Course(offering_unit_code="98", subject_code=subject, course_number=number,
                        supplement_code=supplement, course_string=f"98:{subject}:{number}",
                        title=f"OB COURSE {name}", credits=decimal.Decimal("3.0"), level="U",
                        subject_id=subject_id, source_id=source.id)
        session.add(course)
        session.flush()
        courses[name] = course

    school = School(code=f"OB{s}", name="OB School", campus_code="NB", source_id=source.id)
    session.add(school)
    session.flush()

    def program(code):
        p = Program(school_id=school.id, code=f"{code}{s}", name=f"OB Program {code}",
                    degree_type="BA", source_id=source.id)
        session.add(p)
        session.flush()
        return p

    def version(p, year, published):
        v = ProgramVersion(program_id=p.id, catalog_year=year,
                           sharing_policy="share_across_systems", source_id=source.id,
                           curation_status=CURATED,
                           lifecycle_state="published" if published else "validated",
                           publication_basis="legacy_curated" if published else None)
        session.add(v)
        session.flush()
        return v

    def req(v, code, rtype, *, parent=None, course=None, min_count=None, system="major"):
        r = Requirement(program_version_id=v.id, code=code, name=code.title(),
                        requirement_type=rtype, parent_id=parent.id if parent else None,
                        min_count=min_count, requirement_system=system,
                        source_prose=f"prose {code} {v.id}", curation_status=CURATED,
                        source_id=source.id)
        session.add(r)
        session.flush()
        if course is not None:
            session.add(RequirementCourseOption(requirement_id=r.id, course_id=course.id,
                                                source_id=source.id))
            session.flush()
        return r

    pub, pend, empty, old = program("PUB"), program("PEND"), program("EMPTY"), program("OLD")
    v_pub = version(pub, "2026-2027", True)
    root = req(v_pub, "ROOT", "all_of")
    req(v_pub, "REQ_A", "course", parent=root, course=courses["A"])
    req(v_pub, "REQ_B", "course", parent=root, course=courses["B"])
    req(v_pub, "CORE_D", "choose_n", min_count=1, course=courses["D"], system="core")
    v_pend = version(pend, "2026-2027", False)
    req(v_pend, "REQ_C", "course", course=courses["C"])
    version(empty, "2026-2027", True)
    v_old = version(old, "2025-2026", True)
    req(v_old, "REQ_OLD", "course", course=courses["A"])
    session.commit()

    def key(p):
        return f"{school.code}-{p.code}-ba".lower()

    return {"code": {n: c.course_string for n, c in courses.items()},
            "course_id": {n: c.id for n, c in courses.items()},
            "keys": {"pub": key(pub), "pend": key(pend), "empty": key(empty),
                     "old": key(old)},
            "v_pub": v_pub.id, "v_pend": v_pend.id, "year": "2026-2027"}


def new_account(session) -> uuid.UUID:
    account = UserAccount(identity_provider="oidc", external_subject=f"ob-{uuid.uuid4()}")
    session.add(account)
    session.commit()
    return account.id


def statuses(audit) -> dict[str, str]:
    """requirement_code -> status over the WHOLE audit tree (a dict from the
    API or a DegreeAuditResult)."""
    out: dict[str, str] = {}

    def walk(nodes):
        for n in nodes:
            if isinstance(n, dict):
                out[n["requirement_code"]] = n["status"]
                walk(n["children"])
            else:
                out[n.requirement_code] = n.status.value
                walk(n.children)

    walk(audit["requirements"] if isinstance(audit, dict) else audit.requirements)
    return out
