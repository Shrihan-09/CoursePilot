"""Grounded evaluation cases for the explanation pipeline (Phase 5.13, Part 7).

The correctness criterion is **not** "the explanation reads well". It is:

  1. the deterministic decision is preserved exactly;
  2. a grounded model output is ACCEPTED by the validator;
  3. an ungrounded model output is REJECTED and the deterministic
     explanation is returned instead;
  4. the model is called at most once.

Every case runs the production path - Degree Engine, audit cache, BM25
index, evidence assembly, the real validator, the real fallback - with a
scripted model in place of the network, because no OpenAI credential exists
here. The live equivalent is `test_live_gpt_5_6_luna_...` and it SKIPS.

A case that cannot be grounded in a real deterministic decision is reported
as such rather than faked. See `missing_prerequisite`.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import decimal
import json
import os
import time
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text

from app.models import (
    CatalogCourseEntry,
    Course,
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
# scripted models: what a well-behaved and a badly-behaved model return
# --------------------------------------------------------------------------


class ScriptedModel:
    """The ExplanationModel port, with a scripted response.

    Records every call so a case can assert the model was invoked at most
    once - validation failure must never trigger a retry with a firmer
    prompt.
    """

    def __init__(self, respond, *, available=True):
        self._respond = respond
        self._available = available
        self.calls: list = []

    def is_available(self) -> bool:
        return self._available

    def generate(self, request) -> str:
        self.calls.append(request)
        result = self._respond(request)
        if isinstance(result, Exception):
            raise result
        return result


def grounded(_request) -> str:
    """A response that asserts nothing the evidence did not establish.

    No course keys, no numbers, no requirement codes, no academic claims -
    so it is exactly the shape the validator must ACCEPT.
    """
    return json.dumps({
        "summary": "This course was selected by the degree audit for your plan.",
        "reasons": ["The audit allocated this course to a requirement."],
        "course_information": [],
        "limitations": ["This is a planning aid, not an official audit."],
        "citations": [],
    })


def _json(**fields) -> str:
    base = {"summary": "x", "reasons": [], "course_information": [],
            "limitations": [], "citations": []}
    base.update(fields)
    return json.dumps(base)


# --------------------------------------------------------------------------
# academic fixtures
# --------------------------------------------------------------------------


def _source(session):
    suffix = uuid.uuid4().hex[:8]
    source = DataSource(kind="manual_curation", url=f"synthetic://eval/{suffix}",
                        content_hash=suffix, retrieved_at=dt.datetime.now(dt.UTC),
                        version=1)
    session.add(source)
    session.flush()
    return source


def _soc_course(session, source, *, title, description=None, credits="4.0"):
    import random

    for _ in range(60):
        subject_code = f"{random.randint(100, 999)}"
        number = f"{random.randint(100, 999)}"
        key = f"01:{subject_code}:{number}"
        if not session.execute(text("SELECT 1 FROM course WHERE course_string=:k"),
                               {"k": key}).first():
            break
    else:                                            # pragma: no cover
        pytest.skip("no free SOC-format course key")

    existing = session.execute(
        text("SELECT id FROM subject WHERE code=:c AND offering_unit_code='01'"),
        {"c": subject_code}).first()
    if existing:
        subject_id = existing[0]
    else:
        subject = Subject(code=subject_code, offering_unit_code="01",
                          description="Eval Subject", source_id=source.id)
        session.add(subject)
        session.flush()
        subject_id = subject.id

    course = Course(offering_unit_code="01", subject_code=subject_code,
                    course_number=number, supplement_code="", course_string=key,
                    title=title, credits=decimal.Decimal(credits),
                    subject_id=subject_id, source_id=source.id)
    session.add(course)
    session.flush()
    if description:
        session.add(CatalogCourseEntry(
            course_id=course.id, course_string=key, description=description,
            catalog_year="2026-2027", title=title, source_id=source.id))
        session.flush()
    return course


@dataclass
class Built:
    student: Student
    target: Course
    siblings: list[Course]


def _build(session, *, shape: str) -> Built:
    """One academic shape per evaluation case."""
    source = _source(session)
    suffix = uuid.uuid4().hex[:8]
    school = School(code=f"V{suffix}", name="Eval School", campus_code="NB",
                    source_id=source.id)
    session.add(school)
    session.flush()
    program = Program(school_id=school.id, code=suffix, name="Eval Program",
                      degree_type="BA", source_id=source.id)
    session.add(program)
    session.flush()
    version = ProgramVersion(
        program_id=program.id, catalog_year="2026-2027", source_id=source.id,
        sharing_policy=("share_across_systems" if shape == "shared" else "exclusive"),
    )
    session.add(version)
    session.flush()

    requirement = Requirement(
        program_version_id=version.id, code=f"REQ_{suffix}", name="Core",
        requirement_type="choose_n",
        min_count=(3 if shape == "partial" else 1),
        min_distinct_categories=(2 if shape == "category" else None),
        requirement_system=("core" if shape == "shared" else "major"),
        sort_order=1, source_id=source.id,
    )
    session.add(requirement)
    session.flush()

    with_description = shape != "no_description"
    title = "Data Structures"
    target = _soc_course(session, source, title=title,
                         description=("A course about data structures and "
                                      "algorithms." if with_description else None))
    # Siblings share vocabulary on purpose, so BM25 really does return them.
    siblings = [
        _soc_course(session, source, title=f"Data Structures Variant {i}",
                    description="Also about data structures.")
        for i in range(2)
    ]
    if shape == "already_satisfied" and siblings[0].course_string < target.course_string:
        # Two completed courses compete for one slot and the engine breaks
        # the tie by course string. Keys are random, so without this the
        # sibling won about half the time, the target went unallocated, and
        # the pipeline - correctly - refused to explain it. The ENGINE was
        # deterministic; the FIXTURE was not. Make the target the one the
        # engine allocates, so the case always tests what it is named for.
        target, siblings[0] = siblings[0], target
    for index, course in enumerate([target, *siblings]):
        session.add(RequirementCourseOption(
            requirement_id=requirement.id, course_id=course.id,
            category=(f"CAT{index}" if shape == "category" else None),
            source_id=source.id,
        ))
    session.flush()

    if shape == "excluded":
        session.add(ProgramRule(
            program_version_id=version.id, code=f"RULE_{suffix}",
            name="Exclusion", rule_type="course_exclusion", is_evaluable=True,
            excluded_course_strings=target.course_string, source_id=source.id,
        ))
        session.flush()

    account = UserAccount(identity_provider="oidc",
                          external_subject=f"eval-{uuid.uuid4()}")
    session.add(account)
    session.flush()
    student = Student(external_ref=f"eval-{uuid.uuid4()}",
                      catalog_year=version.catalog_year,
                      program_version_id=version.id, user_id=account.id)
    session.add(student)
    session.flush()

    # "not_recommended" leaves the target untaken and unallocated; every
    # other shape completes the target so the audit allocates it.
    if shape != "not_recommended":
        status = "in_progress" if shape == "partial" else "completed"
        session.add(StudentCourse(
            student_id=student.id, course_id=target.id, term_code="20269",
            status=status, grade=(None if status == "in_progress" else "A"),
            credits_earned=decimal.Decimal("4.0")))
    if shape == "already_satisfied":
        session.add(StudentCourse(
            student_id=student.id, course_id=siblings[0].id, term_code="20259",
            status="completed", grade="A", credits_earned=decimal.Decimal("4.0")))
    session.flush()
    session.commit()
    return Built(student=student, target=target, siblings=siblings)


def _explain(session, built: Built, model, *, explanation_type="why_recommended"):
    from app.services.audit.baseline import compute_baseline
    from app.services.audit.cached_audit import audit_with_cache
    from app.services.explanations import (
        ExplanationType,
        RecommendationExplanationService,
    )
    from app.services.search.index_registry import get_searcher

    audit, _ = audit_with_cache(session, built.student)
    baseline = compute_baseline(session, built.student).satisfied
    searcher, index = get_searcher(session)
    service = RecommendationExplanationService(
        documents_by_key=index.documents_by_key, searcher=searcher, model=model)

    started = time.perf_counter()
    if explanation_type == "why_not_recommended":
        outcome = service.explain_not_recommended(audit, built.target.course_string)
    elif explanation_type == "what_is_course":
        outcome = service.describe_course(built.target.course_string)
    else:
        outcome = service.explain_recommendation(
            audit, built.target.course_string,
            explanation_type=ExplanationType(explanation_type),
            baseline_satisfied=baseline)
    elapsed = (time.perf_counter() - started) * 1000
    return audit, outcome, elapsed


# --------------------------------------------------------------------------
# the case table
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    name: str
    shape: str
    model: object              # a responder, or "unavailable"
    expect_model_used: bool
    explanation_type: str = "why_recommended"
    expect_rejection: bool = False
    #: Exact number of model calls. 0 means the pipeline must refuse BEFORE
    #: the model - either because nothing was decided (ungrounded) or
    #: because no provider is available. Exact, not "at most", so a
    #: regression that starts sending ungrounded requests to the model fails.
    expect_calls: int = 1
    expect_grounded: bool = True


_INJECTED_CLAIM = _json(
    summary="Every graduation requirement is already satisfied.",
    reasons=["All requirements are complete; nothing remains."])

CASES = [
    Case("recommended_course", "ordinary", grounded, True),
    # The course was never taken or allocated, so the engine made NO
    # decision about it. The pipeline must refuse before the model rather
    # than let a model invent a "why not". Measured, not assumed: the first
    # draft of this table expected a model call here and was wrong.
    Case("not_recommended_course", "not_recommended", grounded, False,
         explanation_type="why_not_recommended",
         expect_calls=0, expect_grounded=False),
    Case("already_satisfied", "already_satisfied", grounded, True),
    Case("shared_requirement", "shared", grounded, True),
    Case("category_sensitive", "category", grounded, True),
    # A program rule excludes the course, so the audit does not allocate it
    # and there is no recommendation to explain. Refused before the model.
    Case("excluded_course", "excluded", grounded, False,
         expect_calls=0, expect_grounded=False),
    Case("partial_progress", "partial", grounded, True),
    Case("with_catalog_description", "ordinary", grounded, True),
    Case("without_catalog_description", "no_description", grounded, True),
    Case("similar_vocabulary", "ordinary", grounded, True),
    Case("prompt_injection_claim", "ordinary",
         lambda _r: _INJECTED_CLAIM, False, expect_rejection=True),
    Case("malformed_response", "ordinary",
         lambda _r: "this is { not valid json", False, expect_rejection=True),
    Case("unsupported_claim", "ordinary",
         lambda _r: _json(summary="You have completed all prerequisites."),
         False, expect_rejection=True),
    Case("invented_course_key", "ordinary",
         lambda _r: _json(summary="Take 01:999:999 instead."),
         False, expect_rejection=True),
    Case("provider_failure", "ordinary",
         lambda _r: RuntimeError("provider call failed (APITimeoutError)"), False),
    Case("provider_unavailable", "ordinary", "unavailable", False,
         expect_calls=0),
]


@requires_db
@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_evaluation_case(case: Case) -> None:
    from app.services.audit.engine import DegreeAuditEngine
    from app.services.search.index_registry import get_search_index_registry

    get_search_index_registry().reset()
    with _session() as session:
        built = _build(session, shape=case.shape)

        # The deterministic decision, computed WITHOUT any model.
        before = DegreeAuditEngine(session).audit(built.student).model_dump_json()

        if case.model == "unavailable":
            model = ScriptedModel(grounded, available=False)
        else:
            model = ScriptedModel(case.model)

        audit, outcome, elapsed = _explain(session, built, model,
                                           explanation_type=case.explanation_type)

        # And again after the model ran - it must not have moved.
        after = DegreeAuditEngine(session).audit(built.student).model_dump_json()

    # 1. The deterministic decision is preserved exactly.
    assert before == after, "a model interaction changed the Degree Engine result"

    # 2/3. Grounded output is accepted; ungrounded output is rejected.
    assert outcome.used_model is case.expect_model_used, (
        f"{case.name}: used_model={outcome.used_model}, "
        f"rejections={outcome.rejection_problems}"
    )
    if case.expect_rejection:
        assert outcome.rejection_problems, f"{case.name}: nothing was rejected"
        assert outcome.explanation.generated_by == "deterministic"

    # A rejected or failed model always yields the deterministic answer.
    if not outcome.used_model:
        assert outcome.explanation.generated_by == "deterministic"
    assert outcome.explanation.summary

    # 4. The model is called EXACTLY as often as expected: once for a
    #    grounded request, never for an ungrounded one or with no provider.
    #    Never twice - validation failure must not trigger a retry with a
    #    firmer prompt.
    assert len(model.calls) == case.expect_calls, (
        f"{case.name}: model called {len(model.calls)}x, "
        f"expected {case.expect_calls}"
    )
    assert outcome.grounded is case.expect_grounded, (
        f"{case.name}: grounded={outcome.grounded}"
    )

    # Evidence never includes a sibling course, whatever BM25 matched.
    sibling_keys = {c.course_string for c in built.siblings}
    cited = {f.course_key for f in outcome.evidence.course_facts if f.course_key}
    cited |= {d.course_key for d in outcome.evidence.source_documents}
    assert not (cited & sibling_keys), f"{case.name}: sibling leaked into evidence"

    print(f"\n[eval] {case.name:<28} calls={len(model.calls)} "
          f"used_model={outcome.used_model!s:<5} "
          f"rejected={bool(outcome.rejection_problems)!s:<5} "
          f"grounded={outcome.grounded!s:<5} "
          f"by={outcome.explanation.generated_by:<13} {elapsed:6.1f} ms")


def test_missing_prerequisite_cannot_be_grounded_and_is_not_faked() -> None:
    """**Part 7 case 3, reported honestly rather than constructed.**

    The Degree Engine does not model prerequisites - `app/models/
    requirements.py` says "Deliberately NOT modeled yet: prerequisites as
    structure". There is therefore no deterministic prerequisite DECISION to
    explain, and building a test that pretended otherwise would evaluate a
    fiction.

    What IS verifiable is the safety property: a model that asserts anything
    about prerequisites is rejected, because the validator treats
    "prerequisite" as an academic claim the decision facts never made.
    """
    from app.services.explanations.validation import _ACADEMIC_CLAIMS

    labels = {label for _pattern, label in _ACADEMIC_CLAIMS}
    assert "prerequisite" in labels, (
        "the validator no longer blocks prerequisite claims, which the engine "
        "cannot back"
    )
