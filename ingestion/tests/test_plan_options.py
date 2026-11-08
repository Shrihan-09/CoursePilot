"""Prompt 11 Part A: course options, swaps, locks, undo, "Your choices", course search.

World: the Phase 7.3 workload world (test_workload_levels) - REAL public SOC
records for Spring 2026 / Fall 2026 / Spring 2027, the curated CS B.S. with SAS
Core - and its FABRICATED student (CS 111/112/205, Calc I, Intro to Sociology
passed; Calc II failed in Fall 2026). `nosoc` is the same student without
Intro to Sociology, so Social Analysis is still open. Plans are saved through
the real `save_plan`, so previews, apply and undo run on a real
`student_plan` row.

Spring 2027 for the default student:
    01:198:211 major · 01:640:152 anchor (Calc II retake) · 01:750:203 multi ·
    01:750:205 major · 01:810:135 filler (Core AH)
"""

from __future__ import annotations

import datetime as dt
import re
import time
from types import SimpleNamespace

import pytest
from app.domain.plan_edits import CourseState, PlanChange
from app.domain.planning import FillerPreference, Pace, PlanningConstraints, WorkloadLevel
from app.domain.planning_profile import PlanningProfileInput
from app.models import Course, StudentCourse
from app.models.plans import StudentPlan
from app.services.planning.edits import (
    NothingToUndo,
    StalePreview,
    apply_preview,
    preview_change,
    profile_constraints,
    undo,
)
from app.services.planning.offerings import PLACEABLE
from app.services.planning.options import (
    OptionsContext,
    Similarity,
    course_options,
    liked_courses,
    next_term_choices,
)
from app.services.planning.profile import confirm_profile, get_profile
from app.services.planning.saved import _decode, current_plan, save_plan
from sqlalchemy import select

from .test_workload_levels import HISTORY, SPRING27, _student, world  # noqa: F401

FALL27 = "20279"
EASY_HARD = re.compile(r"\b(easy|easier|hard|harder|difficult)\b", re.IGNORECASE)
FILLER, AH_LATER, SOC = "01:810:135", "01:190:207", "01:920:101"
NOSOC = [h for h in HISTORY if h[0] != SOC]


@pytest.fixture
def today(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("ACADEMIC_TODAY", "2026-11-01")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("ACADEMIC_TODAY")
    get_settings.cache_clear()


def _saved(session, history=HISTORY):
    student = _student(session, history)
    confirm_profile(session, student, PlanningProfileInput(
        workload_level=WorkloadLevel.SEMI_RIGOROUS, filler_preference=FillerPreference.FLEXIBLE,
        max_courses_per_term=6, pace=Pace.BALANCED), dt.datetime.now(dt.UTC))
    session.commit()
    save_plan(session, student, start_term=SPRING27,
              constraints=profile_constraints(session, student))
    session.commit()
    return student


@pytest.fixture
def planned(world, today):  # noqa: F811
    return world, _saved(world)


@pytest.fixture
def nosoc(world, today):  # noqa: F811
    return world, _saved(world, NOSOC)


def _row(session, student) -> StudentPlan:
    return session.scalar(select(StudentPlan).where(StudentPlan.student_id == student.id))


def _ctx(session, student) -> OptionsContext:
    row = _row(session, student)
    return OptionsContext(session, student, _decode(row.result_blob),
                          PlanningConstraints.model_validate(row.constraints))


def _where(saved) -> dict[str, str]:
    return {pc.course: t.term_code for t in saved.result.terms for pc in t.courses}


def _state(saved, course):
    return next(s for s in saved.course_states if s.course == course)


def _apply(session, student, change: PlanChange):
    p = preview_change(session, student, change)
    assert p.valid, p.errors
    session.commit()
    saved = apply_preview(session, student, p.preview_id)
    session.commit()
    return p, saved


# --------------------------------------------------------------------------
# A1: options
# --------------------------------------------------------------------------


def test_options_contain_only_valid_courses_and_an_anchor_has_no_swap(planned):
    session, student = planned
    ctx = _ctx(session, student)
    anchor = course_options(ctx, "01:640:152", SPRING27)
    assert anchor.locked and anchor.locked_reason == "anchor" and anchor.groups == []
    for pc in ctx.plan.terms[0].courses:
        result = course_options(ctx, pc.course, SPRING27)
        for group in result.groups:
            for o in group.options:
                # never already taken, never earlier/this term in the plan
                assert ctx.where.get(o.course, "99999") > SPRING27
                assert o.course not in {k for k, *_ in HISTORY}
                assert ctx.engine.offerings.evidence(o.course, SPRING27).kind in PLACEABLE
                assert o.offering in ("confirmed", "historically offered")
                # re-checked by the planner's own gates, independently
                verdict = ctx.check(SPRING27, {o.course: ctx.fills(o.course)},
                                    replacing=pc.course)[o.course]
                assert verdict.ok and set(o.requirement_codes) <= set(verdict.requirements)
    physics = course_options(ctx, "01:750:203", SPRING27)
    # Gen Physics II needs 203 first: a gate failure is listed, never offered.
    assert {"course": "01:750:204", "reason": "prerequisites_not_met"} in physics.not_available
    assert all(o.course != "01:750:204" for g in physics.groups for o in g.options)


def test_a_filler_offers_the_same_requirement_and_a_course_planned_later(planned):
    session, student = planned
    options = course_options(_ctx(session, student), FILLER, SPRING27)
    same = next(g for g in options.groups if g.kind == "same_requirement")
    assert same.requirement_code == "CORE_AH"
    later = next(o for o in same.options if o.course == AH_LATER)
    assert "Already in your plan for Fall 2027" in later.impact
    assert options.move_terms and SPRING27 not in options.move_terms


def test_a_different_requirement_instead_is_offered_for_a_filler(nosoc):
    session, student = nosoc
    options = course_options(_ctx(session, student), FILLER, SPRING27)
    other = next(g for g in options.groups if g.kind == "different_requirement")
    assert other.requirement_code == "CORE_SCL"
    assert [o.course for o in other.options] == [SOC]
    assert "you'd still need Arts and the Humanities later" in other.options[0].impact


def test_a_different_requirement_instead_defers_the_old_requirement(nosoc):
    session, student = nosoc
    before = current_plan(session, student)
    preview = preview_change(session, student, PlanChange(
        kind="swap", course=FILLER, term_code=SPRING27, replacement=SOC))
    assert preview.valid, preview.errors
    diff = preview.diff
    spring = next(t for t in diff.terms if t.term_code == SPRING27)
    assert SOC in spring.added and FILLER in spring.removed
    after = {line.course: t.term_code for t in diff.terms for line in t.after}
    # Arts and the Humanities is still planned - in a LATER semester.
    assert any(t > SPRING27 for c, t in after.items() if c in (FILLER, AH_LATER))
    assert diff.requirements_lost == []
    # The swapped courses are the student's change, not "forced" moves.
    assert {m.course for m in diff.forced_moves}.isdisjoint({FILLER, SOC})
    # Nothing is saved by a preview.
    assert current_plan(session, student).result == before.result
    assert _row(session, student).revision == before.revision


# --------------------------------------------------------------------------
# A2: preview -> confirm, states, rebuilds, undo
# --------------------------------------------------------------------------


def test_preview_then_apply_and_a_stale_preview_is_refused(planned):
    session, student = planned
    change = PlanChange(kind="swap", course=FILLER, term_code=SPRING27, replacement=AH_LATER)
    first = preview_change(session, student, change)
    second = preview_change(session, student, change)
    session.commit()
    rev = _row(session, student).revision
    saved = apply_preview(session, student, first.preview_id)
    session.commit()
    assert saved.revision == rev + 1
    assert _where(saved)[AH_LATER] == SPRING27
    assert _state(saved, AH_LATER).state is CourseState.USER_SELECTED
    assert FILLER not in {c for c, t in _where(saved).items() if t == SPRING27}
    # computed from the old revision: refused, nothing changes
    with pytest.raises(StalePreview):
        apply_preview(session, student, second.preview_id)
    # an expired preview is refused too
    late = preview_change(session, student, PlanChange(kind="lock", course="01:198:211",
                                                       term_code=SPRING27))
    session.commit()
    with pytest.raises(StalePreview):
        apply_preview(session, student, late.preview_id,
                      now=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1))


def test_user_selected_and_locked_courses_survive_a_rebuild(planned):
    session, student = planned
    _apply(session, student, PlanChange(kind="swap", course=FILLER, term_code=SPRING27,
                                        replacement=AH_LATER))
    _apply(session, student, PlanChange(kind="lock", course="01:198:211", term_code=SPRING27))
    rebuilt = save_plan(session, student, start_term=SPRING27,
                        constraints=profile_constraints(session, student))
    session.commit()
    assert rebuilt.stale_reasons == []
    assert _where(rebuilt)[AH_LATER] == SPRING27
    assert _state(rebuilt, AH_LATER).state is CourseState.USER_SELECTED
    assert _state(rebuilt, "01:198:211").state is CourseState.LOCKED
    assert _state(rebuilt, "01:198:211").locked_reason == "locked"
    assert rebuilt.needs_reselection == []


def test_a_replan_that_drops_a_locked_course_says_so_and_never_replaces_it(planned):
    """Failure injection: the record changes so the LOCKED course and the
    student's CHOSEN course can no longer be planned. Both are reported
    (needs reselection) and both choices are kept - never swapped."""
    session, student = planned
    _apply(session, student, PlanChange(kind="lock", course="01:198:211", term_code=SPRING27))
    _apply(session, student, PlanChange(kind="swap", course=FILLER, term_code=SPRING27,
                                        replacement=AH_LATER))
    for key in ("01:198:211", AH_LATER):
        course = session.scalar(select(Course).where(Course.course_string == key))
        session.add(StudentCourse(student_id=student.id, course_id=course.id, term_code="20269",
                                  status="completed", grade="A", credits_earned=course.credits))
    session.commit()
    rebuilt = save_plan(session, student, start_term=SPRING27,
                        constraints=profile_constraints(session, student))
    session.commit()
    flagged = {r["course"]: r for r in rebuilt.needs_reselection}
    assert set(flagged) == {"01:198:211", AH_LATER}
    assert flagged["01:198:211"]["source"] == "lock"
    assert flagged[AH_LATER]["source"] == "selection"
    assert "already completed" in flagged[AH_LATER]["message"]
    edits = _row(session, student).edits
    assert any(p["course"] == AH_LATER for p in edits["selections"])      # kept, not replaced


def test_undo_restores_the_previous_version(planned):
    session, student = planned
    original = _row(session, student)
    blob, edits, rev = original.result_blob, dict(original.edits), original.revision
    _apply(session, student, PlanChange(kind="swap", course=FILLER, term_code=SPRING27,
                                        replacement=AH_LATER))
    assert current_plan(session, student).last_change.startswith("Swap 01:810:135")
    restored = undo(session, student)
    session.commit()
    row = _row(session, student)
    assert row.result_blob == blob and row.edits == edits and row.revision == rev + 2
    assert _where(restored)[FILLER] == SPRING27
    with pytest.raises(NothingToUndo):
        undo(session, student)


def test_lock_and_unlock_a_semester(planned):
    session, student = planned
    _, locked = _apply(session, student, PlanChange(kind="lock_term", term_code=SPRING27))
    spring = [s for s in locked.course_states if s.term_code == SPRING27]
    assert all(s.state is CourseState.LOCKED for s in spring)
    swap = preview_change(session, student, PlanChange(
        kind="swap", course=FILLER, term_code=SPRING27, replacement=AH_LATER))
    assert not swap.valid and swap.errors[0].startswith("locked:")
    _, unlocked = _apply(session, student, PlanChange(kind="unlock_term", term_code=SPRING27))
    assert _state(unlocked, FILLER).state is CourseState.PLANNER_SELECTED
    assert _state(unlocked, "01:640:152").locked_reason == "anchor"      # an anchor stays


def test_an_anchor_can_only_move(planned):
    session, student = planned
    for kind in ("swap", "remove", "unlock"):
        p = preview_change(session, student, PlanChange(
            kind=kind, course="01:640:152", term_code=SPRING27,
            replacement=AH_LATER if kind == "swap" else None))
        assert not p.valid and "anchor" in p.errors[0]


def test_lighten_a_semester_moves_one_course_out(nosoc):
    session, student = nosoc
    before = current_plan(session, student)
    spring_before = next(t for t in before.result.terms if t.term_code == SPRING27)
    p = preview_change(session, student, PlanChange(kind="lighten_term", term_code=SPRING27))
    assert p.valid, p.errors
    spring = next(t for t in p.diff.terms if t.term_code == SPRING27)
    assert spring.removed == [FILLER] and spring.credits_after < spring_before.credits
    assert "01:640:152" in {line.course for line in spring.after}       # the anchor stays


def test_choose_an_alternative_through_the_same_preview(planned):
    session, student = planned
    choice = next(c for c in current_plan(session, student).result.metadata.alternative_choices
                  if c.requirement_code == "CS_BS_SCIENCE")
    p = preview_change(session, student, PlanChange(
        kind="choose_alternative", requirement_code="CS_BS_SCIENCE",
        alternatives=[a.code for a in choice.chosen]))
    assert p.valid and p.diff is not None
    bad = preview_change(session, student, PlanChange(
        kind="choose_alternative", requirement_code="CS_BS_SCIENCE", alternatives=["NOPE"]))
    assert not bad.valid and bad.errors[0].startswith("invalid_alternative")


def test_a_preview_reproduces_the_plan_and_is_fast(planned):
    """A lock changes nothing else: every other course stays where it is, and
    the preview (the planner re-run with holds) runs well under 3 s."""
    session, student = planned
    started = time.perf_counter()
    p = preview_change(session, student, PlanChange(kind="lock", course="01:198:211",
                                                    term_code=SPRING27))
    elapsed = time.perf_counter() - started
    assert p.valid and p.diff.forced_moves == [] and not p.diff.big
    assert all(t.added == [] and t.removed == [] for t in p.diff.terms)
    assert elapsed < 3.0, f"preview took {elapsed:.2f}s"


# --------------------------------------------------------------------------
# A3: "more like what worked for me", A3b: "Your choices"
# --------------------------------------------------------------------------


def test_similarity_uses_only_the_owners_own_grades_and_can_be_turned_off(nosoc):
    session, student = nosoc
    other = _student(session, [("01:810:135", "20259", "A"), *NOSOC])    # wl-6
    liked = liked_courses(session, student)
    assert {x.course for x in liked} == {k for k, _, g in NOSOC if g in ("A", "B")}
    assert FILLER not in {x.course for x in liked}          # the OTHER student's A
    assert all(x.why in ("did_well", "doing_well") for x in liked)
    ctx = _ctx(session, student)
    note = Similarity(ctx.engine, SPRING27, liked).best(SOC, "INTRO TO SOCIOLOGY")[1]
    # A note names the course and the shared features - never a grade.
    assert note is None or not re.search(r"(^|[\s,:])(A-?|B[+-]?|C[+-]?|D|F)([\s,.]|$)", note)
    del other
    get_profile(session, student).use_grades_for_suggestions = False
    session.commit()
    assert liked_courses(session, student) == []


def test_a_doing_well_mark_counts_for_in_progress_courses_only(nosoc):
    session, student = nosoc
    course = session.scalar(select(Course).where(Course.course_string == "01:198:211"))
    session.add(StudentCourse(student_id=student.id, course_id=course.id, term_code="20269",
                              status="in_progress", doing_well=True))
    session.commit()
    liked = {x.course: x.why for x in liked_courses(session, student)}
    assert liked["01:198:211"] == "doing_well"


def test_your_choices_asks_about_flexible_slots_only(nosoc):
    session, student = nosoc
    term, questions = next_term_choices(_ctx(session, student))
    assert term == SPRING27
    kinds = {q.kind: q for q in questions}
    assert "Calc" in kinds["already_set"].prompt or "CALCULUS" in kinds["already_set"].prompt
    assert "required retake" in kinds["already_set"].prompt
    slot = kinds["slot"]
    assert slot.course == FILLER and {g.kind for g in slot.groups} == {
        "same_requirement", "different_requirement"}
    assert all(len(g.options) <= 3 for g in slot.groups)
    alt = kinds["alternative"]
    assert alt.requirement_code == "CS_BS_SCIENCE" and alt.recommended


def test_no_easy_or_hard_wording_anywhere(nosoc):
    session, student = nosoc
    ctx = _ctx(session, student)
    words = []
    for pc in ctx.plan.terms[0].courses:
        o = course_options(ctx, pc.course, SPRING27)
        words += [x.impact for g in o.groups for x in g.options]
        words += [x.similar_to or "" for g in o.groups for x in g.options]
    _, questions = next_term_choices(ctx)
    words += [q.prompt for q in questions] + [q.reason or "" for q in questions]
    p = preview_change(session, student, PlanChange(kind="swap", course=FILLER,
                                                    term_code=SPRING27, replacement=SOC))
    words += [p.diff.summary, *(m.reason for m in p.diff.forced_moves)]
    assert not EASY_HARD.search(" ".join(words))


def test_an_option_that_fails_a_gate_is_never_offered(planned, monkeypatch):
    """Failure injection: the AH course planned later becomes honors-only."""
    session, student = planned
    from app.services.planning.engine import PlanningEngine

    real = PlanningEngine._honors_only
    monkeypatch.setattr(PlanningEngine, "_honors_only",
                        lambda self, term, key: key == AH_LATER or real(self, term, key))
    options = course_options(_ctx(session, student), FILLER, SPRING27)
    assert all(o.course != AH_LATER for g in options.groups for o in g.options)
    assert {"course": AH_LATER, "reason": "honors_only"} in options.not_available


# --------------------------------------------------------------------------
# A3c: search by what students mean
# --------------------------------------------------------------------------


def _stub_ctx():
    """A resolver world without a database: SAS Core goals and the CS B.S."""
    def req(code, name, parent=None, kind="choose_n"):
        return SimpleNamespace(code=code, name=name, parent_id=parent, requirement_type=kind)
    reqs = {r.code: r for r in [
        req("CORE_AH", "Arts and the Humanities"), req("CORE_HST", "Historical Analysis"),
        req("CORE_CCD", "Diversities and Social Inequalities"),
        req("CORE_WCD", "Discipline-Based Writing"), req("CORE_WCR", "Revision-Based Writing"),
        req("CORE_COLLEGE_WRITING", "College Writing"), req("CORE_NS", "Natural Sciences"),
        req("CORE_QR", "Mathematical or Formal Reasoning"),
        req("CS_ELECTIVES", "Computer Science Electives"), req("CS_211", "Computer Architecture"),
        req("CS_BS_SCIENCE", "Science Requirement (B.S.)", kind="any_of"),
        req("CS_BS_PHYS_A_203", "Physics 01:750:203", parent="sci"),
        req("CS_MATH", "Mathematics Requirement", kind="all_of"),
        req("MATH_250", "Introductory Linear Algebra", parent="math")]}
    by_id = {"sci": "CS_BS_SCIENCE", "math": "CS_MATH"}
    cats = {("CORE_AH", "01:082:105"): {"AHo"}, ("CORE_AH", "01:730:104"): {"AHo", "AHp"},
            ("CORE_HST", "01:082:105"): {"HST"}, ("CORE_CCD", "01:988:130"): {"CCD"},
            ("CORE_WCD", "01:988:130"): {"WCd"}, ("CORE_NS", "01:750:203"): {"NS"},
            ("CORE_WCR", "01:082:105"): {"WCr"}, ("CORE_COLLEGE_WRITING", "01:355:101"): {"WC"},
            ("CORE_QR", "01:640:250"): {"QR"}}
    pools = {}
    for (code, course) in cats:
        pools.setdefault(code, set()).add(course)
    pools.update({"CS_ELECTIVES": {"01:198:440", "14:332:452"}, "CS_211": {"01:198:211"},
                  "CS_BS_PHYS_A_203": {"01:750:203"}, "MATH_250": {"01:640:250"}})
    open_ = sorted(set(pools) - {"CORE_QR"})
    engine = SimpleNamespace(
        pool_categories=cats, requirements=reqs, by_id_code=by_id, pools=pools,
        _is_core=lambda c: c.startswith("CORE"),
        _is_major_node=lambda c: not c.startswith("CORE"))
    plan = SimpleNamespace(terms=[SimpleNamespace(term_code="20271", label="Spring 2027"),
                                  SimpleNamespace(term_code="20279", label="Fall 2027")])
    return SimpleNamespace(engine=engine, open=open_, inactive=frozenset(),
                           names={c: r.name for c, r in reqs.items()}, plan=plan)


@pytest.mark.parametrize(("text", "expect"), [
    ("a different AHo course in Spring 2027, online",
     {"term": "20271", "online": True, "requirements": [["CORE_AH#AHo"]], "topic": None}),
    ("another CS elective that's not 14:332",
     {"requirements": [["CS_ELECTIVES"]], "exclude": ["14:332"], "topic": None}),
    ("something about AI that counts for my major",
     {"topic": "ai", "requirements_any": "CS_ELECTIVES"}),
    ("a writing course that also fills diversity",
     {"requirements": [["CORE_CCD"], ["CORE_COLLEGE_WRITING", "CORE_WCD", "CORE_WCR"]]}),
    ("Fridays off: what Core courses fit?",
     {"avoid_days": ["F"], "requirements_all_core": True, "topic": None}),
])
def test_the_example_queries_are_understood_deterministically(text, expect):
    from app.services.planning.course_search import interpret

    ctx = _stub_ctx()
    it = interpret(ctx, text)
    assert interpret(ctx, text) == it                                    # same words, same meaning
    if "term" in expect:
        assert it.term_code == expect["term"]
    if "online" in expect:
        assert it.online is expect["online"] and not it.async_only
    if "requirements" in expect:
        assert sorted(sorted(g) for g in it.requirements) == sorted(expect["requirements"])
    if "exclude" in expect:
        assert it.exclude == expect["exclude"]
    if "topic" in expect:
        assert it.topic == expect["topic"]
    if "requirements_any" in expect:
        assert any(expect["requirements_any"] in g for g in it.requirements)
    if "avoid_days" in expect:
        assert it.avoid_days == expect["avoid_days"]
    if expect.get("requirements_all_core"):
        assert it.requirements and all(c.startswith("CORE") for c in it.requirements[0])
    assert it.did_you_mean == []


def test_an_ambiguous_phrase_is_asked_back_and_an_unknown_one_is_a_labelled_topic():
    from app.services.planning.course_search import interpret

    ctx = _stub_ctx()
    science = interpret(ctx, "a science course")
    assert science.requirements == [] and science.did_you_mean[0]["phrase"] == "science"
    assert len(science.did_you_mean[0]["options"]) == 2
    music = interpret(ctx, "something about music")
    assert music.requirements == [] and music.topic == "music"
    assert "searched as a topic" in music.notes[0]
    met = interpret(ctx, "a QR course")                                  # QR is already met
    assert met.requirements == [["CORE_QR#QR"]] and "already met" in met.notes[0]


def test_search_returns_only_valid_courses_and_respects_fridays_off(nosoc):
    from app.services.planning.course_search import search_courses

    session, student = nosoc
    ctx = _ctx(session, student)
    result = search_courses(ctx, "Fridays off: what Core courses fit?", term_code=SPRING27)
    assert result.interpretation.avoid_days == ["F"]
    found = {h.course: h for h in result.results}
    # Intro to Sociology (Social Analysis, open) is planned for Fall 2027:
    # found for Spring 2027 too, labelled so "add" means "move it here".
    assert found[SOC].planned_term == FALL27
    for h in result.results:
        verdict = ctx.check(SPRING27, {h.course: ctx.fills(h.course)}, replacing=None)[h.course]
        assert verdict.ok and ctx.where.get(h.course, "99999") > SPRING27
        assert all(c.startswith("CORE") for c in h.requirement_codes)
        assert "no F meetings" in h.why
        assert not EASY_HARD.search(h.why)
    # The listing names the gate a course failed instead of mixing it in.
    assert all(x["reason"] for x in result.not_available)


def test_search_by_topic_uses_the_course_index(nosoc, monkeypatch):
    """The topic goes to the existing BM25 searcher (stubbed here: the test
    world has no published search index); results are still gate-checked."""
    from app.services.planning import course_search

    session, student = nosoc
    hits = [SimpleNamespace(course_key=k) for k in (SOC, "01:750:204", "01:810:135")]
    monkeypatch.setattr("app.services.search.index_registry.get_searcher",
                        lambda s: (SimpleNamespace(search=lambda q, limit: hits), None))
    result = course_search.search_courses(_ctx(session, student), "something about society",
                                          term_code=SPRING27)
    assert result.interpretation.topic == "society"
    courses = [h.course for h in result.results]
    assert SOC in courses
    assert "01:750:204" not in courses                    # prerequisites not met: not mixed in
    assert any(x["course"] == "01:750:204" for x in result.not_available)
    assert "matches 'society'" in next(h for h in result.results if h.course == SOC).why
