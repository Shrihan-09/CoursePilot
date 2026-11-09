"""Prompt 12: real schema and archived SOC, fabricated student, no live DB/model."""
from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.api.v1.routes.plan_changes import BuilderRequest
from app.domain.plan_edits import CourseState, PlanChange
from app.models import Course, CourseOffering
from app.models.sections import CourseSection, SectionMeeting
from app.services.planning.edits import (
    PlanOutOfDate, _refusals, apply_preview, build_edit, edits_of, preview_change, undo,
)
from app.services.planning.semester_builder import browse, term_courses
from .test_plan_options import (
    SPRING27, _ctx, _row, _where, planned, today, world,
)


def test_full_term_browser_counts_every_offered_course_and_exposes_gates(planned):
    session, student = planned
    ctx = _ctx(session, student)
    expected = set(session.scalars(select(Course.course_string).join(CourseOffering).where(
        CourseOffering.term_code == SPRING27, Course.supplement_code == "")).all())
    out = browse(ctx, BuilderRequest(term_code=SPRING27, still_needed=False, limit=100))
    assert out['total_offered'] == out['matching'] == len(expected)
    assert {c['course'] for c in out['items']} == expected
    assert out['eligible'] + out['gated'] + out['selected'] == len(expected)
    assert any(c['gate'] == 'already_taken' and not c['can_add'] for c in out['items'])
    assert any(c['selected'] for c in out['items'])


def test_needed_filter_and_current_membership_adapter(planned):
    session, student = planned
    ctx = _ctx(session, student)
    all_courses = browse(ctx, BuilderRequest(term_code=SPRING27, still_needed=False, limit=100))
    needed = browse(ctx, BuilderRequest(term_code=SPRING27, limit=100))
    assert all(c['selected'] or set(c['requirement_codes']).intersection(ctx.open) for c in needed['items'])
    assert needed['matching'] <= all_courses['matching']
    for c in all_courses['items']:
        if c['can_add']:
            assert set(c['requirement_codes']) <= set(ctx.engine.pools)
        if c['coverage'] == 'unknown':
            assert 'unknown' in c['coverage_label'].lower()
    # Goal filters also cover selected/already-taken rows, whose candidate
    # verdict short-circuits before populating offering evidence.
    goals = sorted({f'{code}#{g}' for (code, _), cats in ctx.engine.pool_categories.items()
                    for g in cats if g and ctx.engine._is_core(code)})
    assert goals
    filtered = browse(ctx, BuilderRequest(term_code=SPRING27, still_needed=False,
                                         requirements=[[goals[0]]], limit=100))
    assert filtered['matching'] <= all_courses['total_offered']


def test_pagination_is_complete_and_stable(planned):
    session, student = planned
    ctx = _ctx(session, student)
    req = dict(term_code=SPRING27, still_needed=False, sort='name', limit=3)
    first = browse(ctx, BuilderRequest(**req))
    keys = []
    for offset in range(0, first['matching'], 3):
        keys += [c['course'] for c in browse(ctx, BuilderRequest(**req, offset=offset))['items']]
    assert len(keys) == len(set(keys)) == first['total_offered']


def test_set_term_preview_is_atomic_revision_bound_and_undoable(planned):
    session, student = planned
    row = _row(session, student)
    ctx = _ctx(session, student)
    original = dict(ctx.where)
    courses = [c.course for c in ctx.term(SPRING27).courses]
    # Keeping every course still turns flexible picks into USER_SELECTED pins.
    p = preview_change(session, student, PlanChange(kind='set_term', term_code=SPRING27,
                       courses=courses, expected_revision=row.revision))
    assert p.valid, p.errors
    assert _ctx(session, student).where == original  # preview did not apply
    saved = apply_preview(session, student, p.preview_id)
    assert all(s.state in (CourseState.USER_SELECTED, CourseState.LOCKED)
               for s in saved.course_states if s.term_code == SPRING27)
    assert _where(saved) == original
    with pytest.raises(PlanOutOfDate):
        preview_change(session, student, PlanChange(kind='set_term', term_code=SPRING27,
                       courses=courses, expected_revision=p.base_revision))
    assert _where(undo(session, student)) == original


def test_anchor_cannot_be_removed_by_semester_save(planned):
    session, student = planned
    ctx = _ctx(session, student)
    courses = [c.course for c in ctx.term(SPRING27).courses if c.course != '01:640:152']
    p = preview_change(session, student, PlanChange(kind='set_term', term_code=SPRING27,
                       courses=courses, expected_revision=_row(session, student).revision))
    assert not p.valid
    assert any('anchor_cannot_be_removed' in e for e in p.errors)


@pytest.mark.parametrize('body', [
    {'courses': ['01:198:111', '01:198:111']}, {'courses': ['invented']},
    {'courses': [], 'expected_revision': None}, {'courses': [], 'pin_explanations': {'01:198:111': 'because'}},
])
def test_semester_change_rejects_invalid_requests(body):
    with pytest.raises(ValidationError):
        PlanChange(kind='set_term', term_code=SPRING27, **({'expected_revision': 1} | body))


def test_refusal_checks_every_selected_course_and_removed_course():
    pc = lambda key: NS(course=key)
    before = NS(terms=[NS(term_code=SPRING27, courses=[pc('01:100:101')])])
    after = NS(terms=[NS(term_code=SPRING27, courses=[pc('01:100:101')])], issues=[])
    change = PlanChange(kind='set_term', term_code=SPRING27, courses=['01:100:102'], expected_revision=1)
    errors = _refusals(after, change, before=before)
    assert len(errors) == 2  # selected missing AND removed course remained


def test_browser_has_no_240_course_shortlist_and_missing_coverage_is_unknown(monkeypatch):
    from app.services.planning import semester_builder as b
    courses = [NS(course_string=f'01:999:{n:03}', title=f'Course {n}', credits=3) for n in range(100, 450)]
    keys = [c.course_string for c in courses]
    monkeypatch.setattr(b, 'term_courses', lambda *args: courses)
    monkeypatch.setattr(b, '_sections', lambda *args: {k: [('12345', 'open', [])] for k in keys})
    monkeypatch.setattr(b, '_blocked', lambda *args: [])
    monkeypatch.setattr(b, 'Similarity', lambda *args: NS(best=lambda *args: (0, None)))
    ctx = NS(session=NS(execute=lambda *args: []), engine=NS(attributes=None, excluded=set()), c=NS(honors=False),
             plan=NS(terms=[], degree_credits=None), liked=[], names={}, open=[], where={})
    ctx.check = lambda term, candidates, **kw: {k: NS(ok=True, reason=None, requirements=[], workload=None, prerequisite=None) for k in candidates}
    adapter = NS(candidates=lambda *args: [])
    out = b.browse(ctx, BuilderRequest(term_code=SPRING27, still_needed=False, offset=300, limit=100), adapter)
    assert out['matching'] == out['total_offered'] == 350
    assert len(out['items']) == 50
    assert all(c['can_add'] and c['coverage'] == 'unknown' for c in out['items'])
    assert all('unknown' in c['coverage_label'] for c in out['items'])
    from app.services.planning.course_search import Interpretation
    monkeypatch.setattr(b, 'interpret', lambda *args, **kwargs: Interpretation(term_code=SPRING27,
                        did_you_mean=[{'phrase': 'science', 'options': []}]))
    ambiguous = b.browse(ctx, BuilderRequest(term_code=SPRING27, text='science', still_needed=False), adapter)
    assert ambiguous['matching'] == 0
    assert ambiguous['interpretation']['did_you_mean'][0]['phrase'] == 'science'


def test_prerequisite_gate_cannot_be_bypassed_by_a_builder_request(planned):
    session, student = planned
    ctx = _ctx(session, student)
    out = browse(ctx, BuilderRequest(term_code=SPRING27, still_needed=False, limit=100))
    gated = next(c for c in out['items'] if c['gate'] == 'prerequisites_not_met')
    courses = [c.course for c in ctx.term(SPRING27).courses] + [gated['course']]
    p = preview_change(session, student, PlanChange(kind='set_term', term_code=SPRING27,
                       courses=courses, expected_revision=_row(session, student).revision,
                       pin_explanations={gated['course']: 'I would like this course'}))
    assert not p.valid
    assert any('prerequisites_not_met' in e for e in p.errors)


def clone_offered_course(session, number):
    """Fabricated offering outside the requirement pools; reuse real-schema defaults."""
    base = session.scalar(select(Course).where(Course.course_string == '01:810:135'))
    def fields(row):
        return {col.name: getattr(row, col.name) for col in row.__table__.columns
                if col.name not in {'id', 'created_at', 'updated_at'}}
    course = Course(**(fields(base) | {'offering_unit_code': '01', 'subject_code': '198',
                     'course_number': number, 'course_string': f'01:198:{number}',
                     'title': 'Fabricated outside-pool course', 'prereq_notes_raw': ''}))
    session.add(course)
    session.flush()
    offering = session.scalar(select(CourseOffering).where(CourseOffering.course_id == base.id,
                                                          CourseOffering.term_code == SPRING27))
    new_offering = CourseOffering(**(fields(offering) | {'course_id': course.id}))
    session.add(new_offering)
    session.flush()
    section = session.scalar(select(CourseSection).where(CourseSection.offering_id == offering.id))
    new_section = CourseSection(**(fields(section) | {'offering_id': new_offering.id, 'index_number': f'99{number}'}))
    session.add(new_section)
    session.flush()
    for meeting in session.scalars(select(SectionMeeting).where(SectionMeeting.section_id == section.id)):
        session.add(SectionMeeting(**(fields(meeting) | {'section_id': new_section.id})))
    session.commit()
    return course.course_string


@pytest.mark.parametrize(('number', 'excluded'), [('199', False), ('105', True)])
def test_outside_pool_course_can_be_selected_with_honest_degree_credit_label(planned, number, excluded):
    session, student = planned
    key = clone_offered_course(session, number)
    ctx = _ctx(session, student)
    out = browse(ctx, BuilderRequest(term_code=SPRING27, still_needed=False, limit=100))
    item = next(c for c in out['items'] if c['course'] == key)
    assert not item['requirement_codes']
    if excluded:
        assert not item['can_add'] and item['can_pin']
        assert item['coverage'] == 'excluded'
        assert "Doesn't count" in item['coverage_label']
    else:
        assert item['can_add'], item
        assert item['coverage'] in ('free_elective', 'unknown')
    courses = [c.course for c in ctx.term(SPRING27).courses if c.course != '01:810:135'] + [key]
    change = PlanChange(kind='set_term', term_code=SPRING27, courses=courses,
                        expected_revision=_row(session, student).revision,
                        pin_explanations={key: 'Taking it for personal interest despite no degree credit'} if excluded else {})
    p = preview_change(session, student, change)
    assert p.valid, p.errors
    saved = apply_preview(session, student, p.preview_id)
    assert _where(saved)[key] == SPRING27
    assert next(s for s in saved.course_states if s.course == key).state is CourseState.USER_SELECTED
    if excluded:
        t = next(t for t in saved.result.terms if t.term_code == SPRING27)
        degree = saved.result.degree_credits
        # The degree total's course bucket omits the explicitly excluded pin.
        assert degree.planned_courses == sum(c.credits for t in saved.result.terms for c in t.courses if c.course != key)
