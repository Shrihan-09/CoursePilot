"""Exhaustive SOC browser; options gates and search are shared, membership is replaceable.

No planner shortlist or topic top-N limits define the universe. A future Prompt 08
index implements MembershipProvider without changing browser or UI contracts.
"""
from __future__ import annotations

from typing import Protocol

from sqlalchemy import select

from app.models import Course, CourseOffering
from app.models.sections import CourseSection, SectionInstructor
from app.services.planning.course_search import (
    Interpretation, RequirementResolver, _blocked, _goal_filters_match, _section_fits, _sections, interpret,
)
from app.services.planning.options import Similarity


class MembershipProvider(Protocol):
    def candidates(self, ctx, course: str) -> list[str]: ...


class ExistingMembership:
    source_name = 'existing_options'
    def candidates(self, ctx, course: str) -> list[str]:
        return [code for code, pool in ctx.engine.pools.items() if code not in ctx.inactive and course in pool]


def term_courses(session, term: str):
    return session.scalars(select(Course).join(CourseOffering).where(
        CourseOffering.term_code == term, Course.supplement_code == ""
    ).distinct().order_by(Course.course_string)).all()


def browse(ctx, request, membership: MembershipProvider | None = None):
    membership = membership or ExistingMembership()
    term = request.term_code
    if request.selection is not None:
        ctx.where = {k: t for k, t in ctx.where.items() if t != term}
        ctx.where.update({k: term for k in request.selection})
    courses = term_courses(ctx.session, term)
    all_keys = [c.course_string for c in courses]
    # Browsing is a new explicit choice: a prior remove is not a permanent ban.
    # This is a request-local engine copy, never a saved constraint mutation.
    if hasattr(ctx.engine, 'constraints'):
        ctx.engine.constraints = ctx.engine.constraints.model_copy(update={'avoid': [
            a for a in ctx.engine.constraints.avoid if a.term_code not in (None, term)]})
    # Engine checks all offerings, including courses outside every requirement pool.
    verdicts = ctx.check(term, {k: membership.candidates(ctx, k) for k in all_keys}, replacing=None)
    sections = _sections(ctx.session, term, all_keys, ctx.engine.attributes, ctx.c.honors)
    blocked = _blocked(ctx, term)
    it = interpret(ctx, request.text, term_code=term) if request.text else Interpretation(term_code=term)
    if request.requirements:
        resolver = RequirementResolver(ctx)
        it.requirements = [[expanded for code in group for expanded in resolver.expand(code, include_closed=not request.still_needed)] for group in request.requirements]
    for key, value in (request.filters.model_dump(exclude_none=True) if request.filters else {}).items():
        if hasattr(it, key):
            setattr(it, key, value)
    topic_hits = {}
    if it.topic:
        from app.services.search.index_registry import get_searcher
        searcher, _ = get_searcher(ctx.session)
        # Exhaustive corpus bound rather than the options service's 300-hit cap.
        topic_hits = {h.course_key: i for i, h in enumerate(searcher.search(it.topic, limit=max(len(searcher.index.documents), 1)))}
    instructors = {}
    if request.text:
        for key, name in ctx.session.execute(select(Course.course_string, SectionInstructor.name)
                .join(CourseOffering, CourseOffering.course_id == Course.id)
                .join(CourseSection, CourseSection.offering_id == CourseOffering.id)
                .join(SectionInstructor, SectionInstructor.section_id == CourseSection.id)
                .where(CourseOffering.term_code == term)):
            instructors.setdefault(key, []).append(name)
    similarity = Similarity(ctx.engine, term, ctx.liked)
    rows = []
    for c in courses:
        if it.did_you_mean and not it.requirements:
            continue  # preserve smart search's ambiguity; never choose a meaning silently
        k = c.course_string
        v = verdicts[k]
        selected_term = ctx.where.get(k)
        selected = selected_term == term
        if it.topic:
            needle = it.topic.casefold()
            literal = f"{k} {c.title} {' '.join(instructors.get(k, []))}".casefold()
            if k not in topic_hits and needle not in literal:
                continue
        current = next((pc for t in ctx.plan.terms if t.term_code == term for pc in t.courses if pc.course == k), None)
        codes = [r.requirement_code for r in current.requirements] if current else list(v.requirements)
        potential = not v.ok and (not selected or current is None) and v.reason != 'core_code_not_listed_for_term'
        if potential:
            # Keep gated members visible under Still-needed; these are potential,
            # not certified completion claims. The gate is always shown beside them.
            codes = membership.candidates(ctx, k)
        if k in ctx.engine.excluded:
            codes = []
        if it.requirements:
            if not all(any(entry.split('#')[0] in codes for entry in g) for g in it.requirements):
                continue
            if v.offering is None:
                v.offering = ctx.engine.offerings.evidence(k, term)
            if not _goal_filters_match(ctx, v, it):
                continue
        if request.still_needed and not set(codes).intersection(ctx.open) and not selected:
            continue
        if it.level and not k.split(':')[-1].startswith(str(it.level)):
            continue
        if it.credits is not None and c.credits != it.credits:
            continue
        secs = sections.get(k, [])
        fitting = [s for s in secs if _section_fits(s[2], it, blocked)]
        if request.delivery == 'hybrid':
            fitting = [s for s in fitting if any(m.campus == 'ONL' or 'ONLINE' in (m.mode_desc or '').upper() for m in s[2])
                       and any(m.campus not in (None, 'ONL') for m in s[2])]
        hard_filters = it.online or it.in_person or it.async_only or it.campus or it.earliest or it.latest or it.avoid_days or request.delivery == 'hybrid'
        if hard_filters and not fitting:
            continue
        status = ('open' if any(s[1] == 'open' for s in fitting) else
                  'closed' if fitting and all(s[1] == 'closed' for s in fitting) else 'unknown')
        if it.open_only and status != 'open':
            continue
        reason = None if v.ok or selected or v.reason == 'core_code_not_listed_for_term' else v.reason
        other_state = getattr(ctx, 'states', {}).get((k, selected_term))
        if selected_term and selected_term != term and other_state and other_state.locked_reason == 'locked':
            reason = 'locked_in_other_term'
        # Blocked-time mismatches remain visible with a reason; no schedule promises.
        if not selected and not reason and blocked and not fitting:
            reason = 'no_section_fits_blocked_times'
        if not selected and not reason and not secs:
            reason = 'no_verified_eligible_sections'
        w = v.workload
        _, note = similarity.best(k, c.title)
        known_degree_total = ctx.plan.degree_credits is not None
        coverage = ('excluded' if k in ctx.engine.excluded else 'mapped' if codes else
                    'free_elective' if known_degree_total else 'unknown')
        if request.degree_credit_only and (coverage == 'excluded' or not known_degree_total):
            continue
        fills = [ctx.names.get(code, code) for code in codes]
        rows.append(dict(course=k, title=c.title, credits=str(c.credits) if c.credits is not None else None,
                         requirement_codes=codes, requirements=fills, coverage=coverage,
                         coverage_label=((('Potential (draft): ' if selected and potential else 'Potential (gated): ' if potential else '') + ' + '.join(fills)) if fills else "Doesn't count toward your degree" if coverage == 'excluded'
                                         else 'Counts only as free elective; other requirement coverage not mapped' if coverage == 'free_elective'
                                         else 'Requirement coverage unknown; needs review'),
                         can_add=not selected and reason is None,
                         can_pin=not selected and reason in {'excluded_by_program', 'prerequisites_need_confirmation', 'special_permission_required'},
                         gate=reason, selected=selected,
                         planned_term=selected_term, prerequisite=v.prerequisite.model_dump(mode='json') if v.prerequisite else None,
                         delivery=list(w.delivery) if w else [], open_status=status,
                         workload_weight=str(w.weight) if w else None,
                         recommended=reason is None and (k in ctx.where or note is not None), similar_to=note))
    if request.sort == 'name':
        rows.sort(key=lambda r: (r['title'].casefold(), r['course']))
    elif request.sort == 'credits':
        rows.sort(key=lambda r: (float(r['credits'] or 0), r['title'].casefold(), r['course']))
    elif request.sort == 'fills':
        rows.sort(key=lambda r: (-len(r['requirement_codes']), r['title'].casefold(), r['course']))
    else:
        rows.sort(key=lambda r: (not r['recommended'], not r['can_add'], topic_hits.get(r['course'], len(all_keys)), r['title'].casefold(), r['course']))
    start = request.offset
    universe = ctx.open if request.still_needed else sorted(code for code, pool in getattr(ctx.engine, 'pools', {}).items()
                                                          if pool and code not in getattr(ctx, 'inactive', set()))
    goals = sorted({(code, goal) for (code, _key), cats in getattr(ctx.engine, 'pool_categories', {}).items()
                    if code in universe for goal in cats if goal})
    requirement_filters = [{'code': code, 'name': ctx.names.get(code, code)} for code in universe]
    requirement_filters += [{'code': f'{code}#{goal}', 'name': f'{ctx.names.get(code, code)} ({goal})'} for code, goal in goals]
    return dict(term_code=term, total_offered=len(courses), matching=len(rows),
                eligible=sum(r['can_add'] for r in rows), gated=sum(bool(r['gate']) for r in rows),
                selected=sum(r['selected'] for r in rows), offset=start,
                items=rows[start:start + request.limit],
                requirements=requirement_filters,
                membership_source=getattr(membership, 'source_name', 'custom_membership'), interpretation=vars(it))
