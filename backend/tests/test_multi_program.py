"""Multi-program audits, program discovery and hypothetical scenarios (Phase 6.0).

The world built here, in real PostgreSQL:

```
school MP ── P1 (supported)       2026-2027: P1_ROOT{P1_X, P1_Y} + CORE_SHARED
         │                        2025-2026: P1OLD_ROOT{P1OLD_Z}            (Case F)
         │                        rule: course E earns no credit            (Case E)
         ├── P2 (pending review)  2026-2027: P2_ROOT{P2_COMPUTING, P2_E, P2_Z}
         │                                    + CORE_SHARED (same prose)
         ├── P4                   2026-2027: no requirements -> unavailable  (Case G)
         └── P5                   2025-2026 only                             (Case F)
school MQ ── P3 (supported)       2026-2027: P3_ROOT{P3_X}

student A: P1 2026-2027, completed X, Y, E, S, W
student B: P1 2026-2027, completed Z
```

The course roles are chosen so each case has exactly one witness:

| course | P1 (actual) | P2 (target) | case |
|---|---|---|---|
| X | P1_X | P2_COMPUTING | C - applies to both, in different roles |
| Y | P1_Y | - | D - must not be inherited by the target |
| E | excluded by rule | P2_E | E - applies differently |
| S | CORE_SHARED | CORE_SHARED | same requirement, same source prose |
| W | - | - | applies in neither |
"""

from __future__ import annotations

import ast
import contextlib
import datetime as dt
import decimal
import hashlib
import os
import pathlib
import re
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.models import (
    Course,
    CurationStatus,
    DataSource,
    Program,
    ProgramRule,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    School,
    Student,
    StudentCourse,
    Subject,
    UserAccount,
)

requires_db = pytest.mark.db

CURATED = CurationStatus.CURATED_FROM_PROSE.value
SHARED_PROSE = "Shared core prose loaded from one source into both versions."


@contextlib.contextmanager
def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    url = os.environ.get(
        "DATABASE_URL_SYNC",
        "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test",
    )
    engine = create_engine(url, poolclass=NullPool)
    try:
        with sessionmaker(engine)() as session:
            yield session
    finally:
        engine.dispose()


# --------------------------------------------------------------------------
# world builder
# --------------------------------------------------------------------------


def _source(session):
    suffix = uuid.uuid4().hex[:8]
    source = DataSource(kind="manual_curation", url=f"synthetic://mp/{suffix}",
                        content_hash=suffix, retrieved_at=dt.datetime.now(dt.UTC),
                        version=1)
    session.add(source)
    session.flush()
    return source


def _course(session, source, title):
    subject_code = uuid.uuid4().hex[:6]
    number = f"{uuid.uuid4().int % 900 + 100}"
    subject = Subject(code=subject_code, offering_unit_code="01",
                      description="MP Subject", source_id=source.id)
    session.add(subject)
    session.flush()
    course = Course(offering_unit_code="01", subject_code=subject_code,
                    course_number=number, supplement_code="",
                    course_string=f"01:{subject_code}:{number}", title=title,
                    credits=decimal.Decimal("3.0"), subject_id=subject.id,
                    source_id=source.id)
    session.add(course)
    session.flush()
    return course


def _version(session, source, program, year, *, curated, sharing="share_across_systems"):
    # Phase 6.3: "supported" means lifecycle `published`. A curated fixture
    # stands for a published version (grandfathered basis, no fake reviewer).
    version = ProgramVersion(program_id=program.id, catalog_year=year,
                             sharing_policy=sharing, source_id=source.id,
                             curation_status=CURATED if curated else
                             CurationStatus.UNVERIFIED.value,
                             lifecycle_state="published" if curated else "parsed",
                             publication_basis="legacy_curated" if curated else None)
    session.add(version)
    session.flush()
    return version


def _req(session, source, version, code, rtype, *, parent=None, courses=(),
         min_count=None, system="major", prose=None, curated=True, order=0):
    req = Requirement(program_version_id=version.id, code=code, name=code.title(),
                      requirement_type=rtype, parent_id=parent.id if parent else None,
                      min_count=min_count, requirement_system=system, sort_order=order,
                      source_prose=prose or f"prose for {code} in {version.id}",
                      curation_status=CURATED if curated else
                      CurationStatus.UNVERIFIED.value,
                      source_id=source.id)
    session.add(req)
    session.flush()
    for course in courses:
        session.add(RequirementCourseOption(requirement_id=req.id, course_id=course.id,
                                            source_id=source.id))
    session.flush()
    return req


def _student(session, version, courses):
    account = UserAccount(identity_provider="oidc", external_subject=f"mp-{uuid.uuid4()}")
    session.add(account)
    session.flush()
    student = Student(external_ref=f"mp-{uuid.uuid4()}", catalog_year="2026-2027",
                      program_version_id=version.id, user_id=account.id)
    session.add(student)
    session.flush()
    for course in courses:
        session.add(StudentCourse(student_id=student.id, course_id=course.id,
                                  term_code="20259", status="completed", grade="A",
                                  credits_earned=decimal.Decimal("3.0")))
    session.flush()
    return student, account


def _world(session) -> dict:
    s = uuid.uuid4().hex[:6]
    src = _source(session)
    school = School(code=f"MP{s}", name="MP School", campus_code="NB", source_id=src.id)
    other = School(code=f"MQ{s}", name="MQ School", campus_code="NB", source_id=src.id)
    session.add_all([school, other])
    session.flush()

    c = {name: _course(session, src, f"MP Course {name}") for name in "XYEZSW"}

    def program(sch, code):
        p = Program(school_id=sch.id, code=f"{code}{s}", name=f"Program {code}",
                    degree_type="BA", source_id=src.id)
        session.add(p)
        session.flush()
        return p

    p1, p2, p3, p4, p5 = (program(school, "P1"), program(school, "P2"),
                          program(other, "P3"), program(school, "P4"),
                          program(school, "P5"))

    # P1 2026-2027 - the actual program, fully curated.
    v1 = _version(session, src, p1, "2026-2027", curated=True)
    root = _req(session, src, v1, "P1_ROOT", "all_of")
    _req(session, src, v1, "P1_X", "course", parent=root, courses=[c["X"]], order=0)
    _req(session, src, v1, "P1_Y", "course", parent=root, courses=[c["Y"]], order=1)
    _req(session, src, v1, "CORE_SHARED", "choose_n", min_count=1, courses=[c["S"]],
         system="core", prose=SHARED_PROSE, order=5)
    session.add(ProgramRule(program_version_id=v1.id, code="P1_EXCL", name="P1 exclusion",
                            rule_type="course_exclusion",
                            excluded_course_strings=c["E"].course_string,
                            curation_status=CURATED, source_id=src.id))

    # P1 2025-2026 - same program, different catalog year, different rules.
    v1_old = _version(session, src, p1, "2025-2026", curated=True)
    old_root = _req(session, src, v1_old, "P1OLD_ROOT", "all_of")
    _req(session, src, v1_old, "P1OLD_Z", "course", parent=old_root, courses=[c["Z"]])

    # P2 2026-2027 - the target, encoded but not yet human-verified.
    v2 = _version(session, src, p2, "2026-2027", curated=False)
    root2 = _req(session, src, v2, "P2_ROOT", "all_of", curated=False)
    _req(session, src, v2, "P2_COMPUTING", "choose_n", parent=root2, min_count=1,
         courses=[c["X"]], curated=False, order=0)
    _req(session, src, v2, "P2_E", "course", parent=root2, courses=[c["E"]],
         curated=False, order=1)
    _req(session, src, v2, "P2_Z", "course", parent=root2, courses=[c["Z"]],
         curated=False, order=2)
    _req(session, src, v2, "CORE_SHARED", "choose_n", min_count=1, courses=[c["S"]],
         system="core", prose=SHARED_PROSE, curated=False, order=5)

    # P3 - another school.
    v3 = _version(session, src, p3, "2026-2027", curated=True)
    _req(session, src, v3, "P3_X", "course", courses=[c["X"]])

    # P4 - a version with no requirements: unavailable.
    _version(session, src, p4, "2026-2027", curated=True)

    # P5 - only an older catalog year exists.
    v5 = _version(session, src, p5, "2025-2026", curated=True)
    _req(session, src, v5, "P5_X", "course", courses=[c["X"]])

    a, account_a = _student(session, v1, [c[n] for n in "XYESW"])
    b, account_b = _student(session, v1, [c["Z"]])
    session.commit()

    def key(p, sch):
        return f"{sch.code}-{p.code}-ba".lower()

    return {
        "courses": {n: course.course_string for n, course in c.items()},
        "keys": {"p1": key(p1, school), "p2": key(p2, school), "p3": key(p3, other),
                 "p4": key(p4, school), "p5": key(p5, school)},
        "a": a.id, "b": b.id, "account_a": account_a.id, "account_b": account_b.id,
        "v1": v1.id, "v2": v2.id,
    }


@pytest.fixture
def world():
    with _session() as session:
        return _world(session)


def _student_row(session, student_id) -> Student:
    return session.get(Student, student_id)


def _codes(result) -> set[str]:
    out = set()

    def walk(nodes):
        for n in nodes:
            out.add(n.requirement_code)
            walk(n.children)

    walk(result.requirements)
    return out


def _applied(result) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for allocation in result.allocation:
        out.setdefault(allocation.course.course_string, set()).add(allocation.requirement_code)
    return out


# ==========================================================================
# Part 5 - the coupling, and the smallest boundary that removes it
# ==========================================================================


@requires_db
def test_case_a_default_audit_is_unchanged_by_the_new_parameter(world) -> None:
    """Omitting `program_version` and passing the student's own are identical."""
    from app.services.audit.engine import DegreeAuditEngine

    with _session() as session:
        student = _student_row(session, world["a"])
        default = DegreeAuditEngine(session).audit(student)
        explicit = DegreeAuditEngine(session).audit(
            student, program_version=session.get(ProgramVersion, world["v1"]))
    assert default.model_dump_json() == explicit.model_dump_json()


@requires_db
def test_case_a_scenario_of_the_current_program_equals_the_actual_audit(world) -> None:
    from app.services.audit.cached_audit import audit_with_cache
    from app.services.scenarios import run_scenario_audit

    with _session() as session:
        student = _student_row(session, world["a"])
        actual, _ = audit_with_cache(session, student, use_cache=False)
        scenario = run_scenario_audit(session, student, world["keys"]["p1"])

    assert scenario.target.is_current_program is True
    assert scenario.audit.model_dump_json() == actual.model_dump_json()
    assert [a.code for a in scenario.assumptions] == ["hypothetical"]


# ==========================================================================
# Part 20 - one engine, two programs, no branching
# ==========================================================================


@requires_db
def test_one_engine_instance_evaluates_two_program_versions(world) -> None:
    """Case B, and the genericity claim, on a single engine object."""
    from app.services.audit.engine import DegreeAuditEngine

    with _session() as session:
        student = _student_row(session, world["a"])
        engine = DegreeAuditEngine(session)
        a = engine.audit(student, program_version=session.get(ProgramVersion, world["v1"]))
        b = engine.audit(student, program_version=session.get(ProgramVersion, world["v2"]))

    assert a.program_name == "Program P1" and b.program_name == "Program P2"
    assert _codes(a) == {"P1_ROOT", "P1_X", "P1_Y", "CORE_SHARED"}
    assert _codes(b) == {"P2_ROOT", "P2_COMPUTING", "P2_E", "P2_Z", "CORE_SHARED"}
    # Every allocation belongs to the version that produced it.
    assert {x.requirement_code for x in a.allocation} <= _codes(a)
    assert {x.requirement_code for x in b.allocation} <= _codes(b)


@requires_db
def test_the_internal_baseline_audit_uses_the_target_version(world, monkeypatch) -> None:
    """The optimizer's "already earned" baseline must come from the SAME rules.

    Before Phase 6.0 the nested baseline audit re-read the student's enrolled
    version, which was harmless only because nothing else could be audited.
    If a scenario's baseline silently came from the enrolled program, the
    optimizer would protect CS requirements while allocating Mathematics
    courses. No other test observes this - it was found by mutating the call
    and watching the suite stay green - so it is pinned here directly.
    """
    from app.services.audit.engine import DegreeAuditEngine

    seen: list[tuple[frozenset | None, object]] = []
    original = DegreeAuditEngine.audit

    def spy(self, student, *, statuses=None, program_version=None):
        seen.append((statuses, program_version.id if program_version else None))
        return original(self, student, statuses=statuses, program_version=program_version)

    monkeypatch.setattr(DegreeAuditEngine, "audit", spy)
    with _session() as session:
        student = _student_row(session, world["a"])
        DegreeAuditEngine(session).audit(
            student, program_version=session.get(ProgramVersion, world["v2"]))

    nested = [version for statuses, version in seen if statuses is not None]
    assert nested, "the optimizer never computed a baseline"
    assert set(nested) == {world["v2"]}


def test_the_audit_engine_contains_no_program_specific_literals() -> None:
    """No `if program == "CS"`: scan every string constant the engine uses.

    Docstrings and comments cite the Rutgers prose a rule came from, which is
    documentation. A string CONSTANT that is a subject code, a course key or a
    curated requirement code would be program logic, and would make the engine
    behave differently for one major. There must be none.
    """
    audit_dir = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "audit"
    program_like = re.compile(
        r"^(\d{3}|\d{2}:\d{3}:\d{3}|(CS|MATH|CORE|SAS)_[A-Z0-9_]+|198|640)$"
    )
    offenders = []
    for path in sorted(audit_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
            and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings and program_like.match(node.value)):
                offenders.append(f"{path.name}:{node.lineno} {node.value!r}")
    assert offenders == [], offenders


# ==========================================================================
# Part 19 - realistic cases C to G
# ==========================================================================


@requires_db
def test_cases_c_d_e_the_target_interprets_the_record_independently(world) -> None:
    from app.services.scenarios import run_scenario_audit

    x, y, e = (world["courses"][k] for k in "XYE")
    with _session() as session:
        student = _student_row(session, world["a"])
        from app.services.audit.engine import DegreeAuditEngine

        actual = DegreeAuditEngine(session).audit(student)
        target = run_scenario_audit(session, student, world["keys"]["p2"]).audit

    cur, tgt = _applied(actual), _applied(target)
    # C: one course, two different roles.
    assert cur[x] == {"P1_X"} and tgt[x] == {"P2_COMPUTING"}
    # D: Y's allocation is NOT inherited - nothing in P2 can use it.
    assert cur[y] == {"P1_Y"} and y not in tgt
    assert y in {ref.course_string for ref in target.unallocated_courses}
    # E: excluded under P1, required under P2.
    assert e in {ref.course_string for ref in actual.excluded_courses}
    assert tgt[e] == {"P2_E"}
    assert actual.credits_excluded == decimal.Decimal("3.0")
    assert target.credits_excluded == decimal.Decimal("0")


@requires_db
def test_case_f_catalog_year_selects_the_right_version(world) -> None:
    from app.services.scenarios import CatalogYearUnavailable, run_scenario_audit

    with _session() as session:
        student = _student_row(session, world["a"])
        old = run_scenario_audit(session, student, world["keys"]["p1"], "2025-2026")
        default = run_scenario_audit(session, student, world["keys"]["p1"])

        # Only 2025-2026 exists for P5: no silent fallback to it.
        with pytest.raises(CatalogYearUnavailable) as caught:
            run_scenario_audit(session, student, world["keys"]["p5"])

    assert old.audit.catalog_year == "2025-2026"
    assert _codes(old.audit) == {"P1OLD_ROOT", "P1OLD_Z"}
    assert old.target.is_current_program is False
    assert "catalog_year_differs" in [a.code for a in old.assumptions]
    # No year given -> the student's own year, which here is the current version.
    assert default.audit.catalog_year == "2026-2027"
    assert caught.value.available == ["2025-2026"]


@requires_db
def test_case_g_unavailable_and_unknown_programs_fail_honestly(world) -> None:
    from app.services.scenarios import ProgramNotEvaluable, ProgramNotFound, run_scenario_audit

    with _session() as session:
        student = _student_row(session, world["a"])
        with pytest.raises(ProgramNotEvaluable):
            run_scenario_audit(session, student, world["keys"]["p4"])
        with pytest.raises(ProgramNotFound):
            run_scenario_audit(session, student, "zz9-nope-ba")


@requires_db
def test_a_program_in_another_school_is_evaluated_with_the_boundary_stated(world) -> None:
    """Evaluating requirements is not a claim that transfer is allowed."""
    from app.services.scenarios import run_scenario_audit

    with _session() as session:
        student = _student_row(session, world["a"])
        scenario = run_scenario_audit(session, student, world["keys"]["p3"])

    codes = [a.code for a in scenario.assumptions]
    assert "different_school" in codes and "admission_not_modeled" in codes
    assert _applied(scenario.audit)[world["courses"]["X"]] == {"P3_X"}


@requires_db
def test_a_pending_review_target_says_so(world) -> None:
    from app.services.scenarios import run_scenario_audit

    with _session() as session:
        student = _student_row(session, world["a"])
        scenario = run_scenario_audit(session, student, world["keys"]["p2"])
    assert scenario.target.support_status == "pending_review"
    assert "requirements_pending_review" in [a.code for a in scenario.assumptions]


# ==========================================================================
# Part 11 - Case I: a scenario writes nothing
# ==========================================================================

_STATE_TABLES = (
    "student", "student_course", "user_account", "student_link_event",
    "school", "program", "program_version", "requirement",
    "requirement_course_option", "program_rule", "course", "catalog_course_entry",
    "data_source", "rules_version", "search_version",
)


def _snapshot(session) -> dict[str, str]:
    out = {}
    for table in _STATE_TABLES:
        rows = session.execute(text(f"SELECT * FROM {table} ORDER BY 1")).all()
        out[table] = hashlib.sha256(repr(rows).encode()).hexdigest()
    return out


@requires_db
def test_case_i_scenarios_leave_every_academic_table_unchanged(world) -> None:
    from app.services.scenarios import compare_with_current, run_scenario_audit

    with _session() as session:
        before = _snapshot(session)
        cache_before = session.execute(
            text("SELECT * FROM student_audit_cache WHERE student_id = :s"),
            {"s": world["a"]}).all()

        student = _student_row(session, world["a"])
        for key in ("p2", "p3"):
            run_scenario_audit(session, student, world["keys"][key])
        run_scenario_audit(session, student, world["keys"]["p1"], "2025-2026")
        cache_after_scenarios = session.execute(
            text("SELECT * FROM student_audit_cache WHERE student_id = :s"),
            {"s": world["a"]}).all()
        session.commit()

    with _session() as session:
        after = _snapshot(session)
        student = session.get(Student, world["a"])
        binding = (student.program_version_id, student.catalog_year)

    assert before == after, [t for t in before if before[t] != after[t]]
    assert binding == (world["v1"], "2026-2027")
    # Scenarios alone never touch the audit cache.
    assert cache_before == cache_after_scenarios

    # A comparison computes the ACTUAL audit through the cache, which may
    # write that student's cache row - the one sanctioned write. Nothing else.
    with _session() as session:
        compare_with_current(session, _student_row(session, world["a"]), world["keys"]["p2"])
        session.commit()
    with _session() as session:
        assert _snapshot(session) == before


# ==========================================================================
# Part 12 - Case J: cache separation
# ==========================================================================


@requires_db
def test_case_j_the_actual_cache_key_has_no_target_dimension(world) -> None:
    """Why scenarios must bypass `student_audit_cache`.

    The key is computed from the Student alone. Were a scenario to go through
    `audit_with_cache`, it would be stored under exactly this key and served
    later as the student's real audit. This test pins that fact, so anyone
    who routes scenarios through the cache has to read why they must not.
    """
    from app.services.audit.cache import AuditCacheKey

    with _session() as session:
        student = _student_row(session, world["a"])
        key_before = AuditCacheKey.compute(session, student)
        from app.services.scenarios import run_scenario_audit

        run_scenario_audit(session, student, world["keys"]["p2"])
        key_after = AuditCacheKey.compute(session, student)
    assert key_before == key_after
    assert str(world["v2"]) not in repr(key_before)


@requires_db
def test_case_j_scenarios_cannot_contaminate_the_actual_cached_audit(world) -> None:
    """Warm the actual cache, run scenarios for two other programs, read again.

    Fails if any scenario result was written to, or served from, the cache.
    """
    from app.services.audit.cached_audit import audit_with_cache
    from app.services.scenarios import run_scenario_audit

    with _session() as session:
        student = _student_row(session, world["a"])
        fresh, _ = audit_with_cache(session, student, use_cache=False)
        audit_with_cache(session, student)                       # warm
        session.commit()

    with _session() as session:
        student = _student_row(session, world["a"])
        p2 = run_scenario_audit(session, student, world["keys"]["p2"]).audit
        p3 = run_scenario_audit(session, student, world["keys"]["p3"]).audit
        served, from_cache = audit_with_cache(session, student)

    assert from_cache is True
    assert served.model_dump_json() == fresh.model_dump_json()
    assert served.program_name == "Program P1"
    assert served.model_dump_json() not in (p2.model_dump_json(), p3.model_dump_json())


@requires_db
def test_case_j_two_targets_never_share_a_result(world) -> None:
    from app.services.scenarios import run_scenario_audit

    with _session() as session:
        student = _student_row(session, world["a"])
        first = [run_scenario_audit(session, student, world["keys"][k]).audit
                 for k in ("p2", "p3")]
        again = [run_scenario_audit(session, student, world["keys"][k]).audit
                 for k in ("p3", "p2")]
    assert first[0].program_name == again[1].program_name == "Program P2"
    assert first[1].program_name == again[0].program_name == "Program P3"


# ==========================================================================
# Part 13 - deterministic comparison
# ==========================================================================


@requires_db
def test_comparison_reads_the_difference_off_two_audits(world) -> None:
    from app.services.scenarios import compare_with_current

    names = {v: k for k, v in world["courses"].items()}
    with _session() as session:
        student = _student_row(session, world["a"])
        _scenario, _actual, comparison = compare_with_current(
            session, student, world["keys"]["p2"])

    def which(rows):
        return sorted(names[r.course_string] for r in rows)

    assert which(comparison.courses_applied_in_both) == ["S", "X"]
    assert which(comparison.courses_applied_only_current) == ["Y"]
    assert which(comparison.courses_applied_only_target) == ["E"]
    assert which(comparison.courses_applied_in_neither) == ["W"]
    e_row = comparison.courses_applied_only_target[0]
    assert e_row.excluded_by_current is True and e_row.excluded_by_target is False

    assert [r.code for r in comparison.requirements_in_both] == ["CORE_SHARED"]
    assert {r.code for r in comparison.requirements_only_current} == {
        "P1_ROOT", "P1_X", "P1_Y"}
    assert {r.code for r in comparison.requirements_only_target} == {
        "P2_ROOT", "P2_COMPUTING", "P2_E", "P2_Z"}
    z = next(r for r in comparison.requirements_only_target if r.code == "P2_Z")
    assert z.status == "unsatisfied"


def test_the_same_code_from_different_prose_is_not_a_shared_requirement() -> None:
    """The real CS and Mathematics definitions both contain `MATH_151`."""
    from app.domain.audit import AuditStatus, DegreeAuditResult, RequirementResult
    from app.services.scenarios import compare_audits

    def result(prose):
        return DegreeAuditResult(
            program_name="p", program_code="c", degree_type="BA", catalog_year="2026-2027",
            status=AuditStatus.INCOMPLETE,
            requirements=[RequirementResult(
                requirement_code="MATH_151", requirement_name="Calculus I",
                requirement_type="course", status="unsatisfied", reason="r",
                source_prose=prose)])

    comparison = compare_audits(result("CS page sentence"), result("Math page sentence"))
    assert comparison.requirements_in_both == []
    assert len(comparison.requirements_only_current) == 1


def test_the_comparison_contains_no_ranking_or_estimate() -> None:
    from app.domain.scenario import ProgramComparison

    fields = set(ProgramComparison.model_fields)
    for forbidden in ("score", "rank", "recommend", "semesters", "better", "best"):
        assert not any(forbidden in f for f in fields), forbidden


# ==========================================================================
# Part 8/9 - program discovery
# ==========================================================================


@requires_db
def test_program_discovery_derives_support_status_from_curation(world) -> None:
    from app.services.programs import get_program

    with _session() as session:
        p1 = get_program(session, world["keys"]["p1"])
        p2 = get_program(session, world["keys"]["p2"])
        p4 = get_program(session, world["keys"]["p4"])

    assert [v.catalog_year for v in p1.versions] == ["2026-2027", "2025-2026"]
    assert [v.support_status.value for v in p1.versions] == ["supported", "supported"]
    assert p2.versions[0].support_status.value == "pending_review"
    assert p4.versions[0].support_status.value == "unavailable"
    assert p1.program_key == world["keys"]["p1"]


def test_support_status_follows_the_lifecycle_not_the_label() -> None:
    from app.services.programs import support_status

    assert support_status("published", [CURATED, CURATED], 2).value == "supported"
    # A curated_from_prose LABEL no longer makes a program supported.
    for state in ("parsed", "validated", "reviewed", "needs_rereview"):
        assert support_status(state, [CURATED, CURATED], 2).value == "pending_review"
    assert support_status("published", [CURATED, "synthetic"], 2).value == "unavailable"
    assert support_status("published", [CURATED], 0).value == "unavailable"


def test_program_keys_are_natural_and_strict() -> None:
    from app.services.programs import InvalidProgramKey, parse_program_key, program_key

    assert program_key("SAS", "640", "BA") == "sas-640-ba"
    assert program_key("SAS", "640", "BA", "option-a") == "sas-640-ba-option-a"
    assert parse_program_key("sas-640-ba") == ("sas", "640", "ba", "")
    assert parse_program_key("sas-640-ba-option-a") == ("sas", "640", "ba", "option-a")
    for bad in ("sas-640", "sas-640-ba-", "sas_640-ba-", "../etc-x-y", "sas--640-ba", ""):
        with pytest.raises(InvalidProgramKey):
            parse_program_key(bad)


# ==========================================================================
# Parts 15/16 - API, ownership and privacy (Case H)
# ==========================================================================


def _client(app, account_id):
    from app.api.security import Principal, get_principal

    app.dependency_overrides[get_principal] = lambda: Principal(
        account_id=account_id, subject="s", issuer="https://issuer.test", provider="oidc")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@requires_db
async def test_program_discovery_api_exposes_natural_keys_only(world) -> None:
    from app.main import create_app

    async with _client(create_app(), world["account_a"]) as ac:
        listing = await ac.get("/api/v1/programs")
        one = await ac.get(f"/api/v1/programs/{world['keys']['p2']}")
        missing = await ac.get("/api/v1/programs/zz9-nope-ba")
        malformed = await ac.get("/api/v1/programs/not_a_key")

    assert listing.status_code == 200 and one.status_code == 200
    keys = {p["program_key"] for p in listing.json()["programs"]}
    assert set(world["keys"].values()) <= keys
    assert one.json()["versions"][0]["support_status"] == "pending_review"
    assert missing.status_code == 404 and malformed.status_code == 422
    body = listing.text
    for identifier in (str(world["v1"]), str(world["v2"]), str(world["a"])):
        assert identifier not in body


async def test_program_discovery_requires_authentication() -> None:
    from app.main import create_app

    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as ac:
        assert (await ac.get("/api/v1/programs")).status_code == 401
        response = await ac.post("/api/v1/student/scenarios/audit",
                                 json={"program_key": "sas-640-ba"})
        assert response.status_code == 401


@requires_db
async def test_case_h_each_student_gets_their_own_scenario(world) -> None:
    """A and B ask the same question and get answers about their own records."""
    from app.main import create_app

    app = create_app()
    body = {"program_key": world["keys"]["p2"]}
    async with _client(app, world["account_a"]) as ac:
        a = (await ac.post("/api/v1/student/scenarios/audit", json=body)).json()
    async with _client(app, world["account_b"]) as ac:
        b = (await ac.post("/api/v1/student/scenarios/audit", json=body)).json()

    def applied(payload):
        return {x["course"]["course_string"] for x in payload["audit"]["allocation"]}

    x, e, s, z = (world["courses"][k] for k in "XESZ")
    assert applied(a) == {x, e, s}
    assert applied(b) == {z}


@requires_db
@pytest.mark.parametrize("field", [
    "student_id", "student_ref", "external_ref", "account_id", "subject",
    "user_id", "principal",
])
async def test_case_h_no_body_field_can_select_a_student(world, field) -> None:
    """A in the header, B in the body: rejected outright, never half-honoured."""
    from app.main import create_app

    with _session() as session:
        victim = session.get(Student, world["b"])
        values = {
            "student_id": str(victim.id), "student_ref": victim.external_ref,
            "external_ref": victim.external_ref, "account_id": str(world["account_b"]),
            "subject": "b-subject", "user_id": str(world["account_b"]),
            "principal": "b",
        }
    async with _client(create_app(), world["account_a"]) as ac:
        response = await ac.post(
            "/api/v1/student/scenarios/compare",
            json={"program_key": world["keys"]["p2"], field: values[field]})
    assert response.status_code == 422
    assert victim.external_ref not in response.text


@requires_db
async def test_scenario_api_errors_are_honest(world) -> None:
    from app.main import create_app

    with _session() as session:
        unlinked = UserAccount(identity_provider="oidc", external_subject=f"u-{uuid.uuid4()}")
        session.add(unlinked)
        session.commit()
        unlinked_id = unlinked.id

    app = create_app()
    async with _client(app, world["account_a"]) as ac:
        unknown = await ac.post("/api/v1/student/scenarios/audit",
                                json={"program_key": "zz9-nope-ba"})
        no_year = await ac.post("/api/v1/student/scenarios/audit",
                                json={"program_key": world["keys"]["p5"]})
        empty = await ac.post("/api/v1/student/scenarios/audit",
                              json={"program_key": world["keys"]["p4"]})
        bad_year = await ac.post("/api/v1/student/scenarios/audit",
                                 json={"program_key": world["keys"]["p1"],
                                       "catalog_year": "latest"})
    async with _client(app, unlinked_id) as ac:
        orphan = await ac.post("/api/v1/student/scenarios/audit",
                               json={"program_key": world["keys"]["p2"]})

    assert unknown.status_code == 404
    assert no_year.status_code == 404 and "2025-2026" in no_year.json()["detail"]
    assert empty.status_code == 422
    assert bad_year.status_code == 422
    assert orphan.status_code == 409


@requires_db
async def test_compare_api_returns_the_deterministic_comparison(world) -> None:
    from app.main import create_app

    async with _client(create_app(), world["account_a"]) as ac:
        response = await ac.post("/api/v1/student/scenarios/compare",
                                 json={"program_key": world["keys"]["p2"]})
        audit = await ac.get("/api/v1/student/audit")

    assert response.status_code == 200
    payload = response.json()
    assert payload["target"]["program_key"] == world["keys"]["p2"]
    assert payload["comparison"]["current"]["program_name"] == "Program P1"
    assert payload["comparison"]["target"]["program_name"] == "Program P2"
    # The real audit is still the real program after a comparison.
    assert audit.json()["program_name"] == "Program P1"
