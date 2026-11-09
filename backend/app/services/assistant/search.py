"""Requirement-search intent and zero-result explanations, using existing tools.

Generic format + requirement requests have no catalog topic. Resolve them in
code rather than allowing a model to turn 'asynch' into a BM25 topic or turn
alternative requirements into an accidental intersection.
"""
import re

from app.services.assistant.cards import option_card
from app.services.assistant.contracts import AssistantReply, Chip, Question, term_label
from app.services.assistant.router import extract_known


def is_requirement_search(text):
    k = extract_known(text)
    return k.format in {'async', 'online', 'in_person'} and k.topic is None and not re.search(
        r'\b(history|historical|writing|discipline|revision|science|humanities|core|major|math|electives?)\b', text, re.I) and bool(
        re.search(r'\b(a|any|another|different|dif|remaining|unmet|open)\s+requirements?\b', text, re.I)) and bool(
        re.search(r'\b(fill\w*|count\w*|satisf\w*|options?|courses?|classes)\b', text, re.I))


def describe(outcome):
    args, it = outcome.args, outcome.data.get('interpretation') or {}
    f = args.get('filters') or {}
    groups = args.get('requirements') or it.get('requirements') or []
    names = outcome.data.get('requirement_names') or {}
    requirements = ' AND '.join('any of [' + ', '.join(names.get(c, c) for c in g) + ']' for g in groups)
    bits = [term_label(it.get('term_code') or args.get('term_code')) or 'selected term',
            requirements or 'any remaining requirement',
            'async only' if f.get('async_only') or it.get('async_only') else
            'online including async' if f.get('online') or it.get('online') else
            'in person' if f.get('in_person') or it.get('in_person') else 'any format',
            'open sections only' if f.get('open_only') or it.get('open_only') else 'any seat status (open-only off)']
    for key in ('campus', 'earliest', 'latest', 'avoid_days', 'level', 'credits'):
        value = f.get(key) or it.get(key)
        if value:
            bits.append(f'{key.replace("_", " ")}: {value}')
    if args.get('topic') or args.get('keywords'):
        bits.append('topic: ' + str(args.get('topic') or ', '.join(args['keywords'])))
    bits.append('completed/in-progress and courses already in this or an earlier term excluded; later planned courses may move here; saved blocked times respected')
    return '; '.join(bits)


def empty_reply(outcome):
    args = {k: v for k, v in outcome.args.items() if k != 'keywords'}
    it = outcome.data.get('interpretation') or {}
    args['text'] = ''
    args['filters'] = {k: it.get(k, v) for k, v in (args.get('filters') or {}).items()}
    for key in ('online', 'async_only', 'in_person', 'campus', 'earliest', 'latest', 'avoid_days', 'level', 'credits', 'open_only', 'fills_two'):
        if it.get(key) not in (None, False, [], ''):
            args['filters'][key] = it[key]
    if it.get('requirements'):
        args['requirements'] = it['requirements']
    if it.get('topic') is not None:
        args['topic'] = it['topic']
    return AssistantReply(
        text='No courses passed this search. Filters used: ' + describe(outcome) + '. You can relax one filter; prerequisites and restrictions still apply.',
        generated_by='template',
        questions=[Question(id='search_relax', prompt='Relax one filter?', chips=[
            Chip(label='Any format (including hybrid)', question='search_relax', value='format'),
            Chip(label='Any remaining requirement', question='search_relax', value='requirement'),
        ])], flow={'kind': 'course_search', 'args': args})


def run(ctx, text='', *, previous=None, relax=None, context_term=None, builder_selection=None):
    if previous and relax:
        args = dict(previous['args'])
        if relax == 'format':
            args['filters'] = {k: v for k, v in (args.get('filters') or {}).items()
                               if k not in {'online', 'async_only', 'in_person'}}
            args['text'] = ''
        elif relax == 'requirement':
            args['requirements'] = None
            args['text'] = ''
    else:
        plan = ctx.call('get_plan').data
        progress = ctx.call('get_progress').data
        known = extract_known(text)
        m = re.search(r'\b(spring|summer|fall|winter)\s+(20\d\d)\b', text, re.I)
        term = m[2] + {'spring': '1', 'summer': '7', 'fall': '9', 'winter': '0'}[m[1].lower()] if m else context_term or (plan.get('terms') or [{}])[0].get('term_code')
        codes = [r['code'] for r in progress.get('requirements', []) if r.get('status') in {'open', 'planned', 'needs_review'}]
        filters = ({'async_only' if known.format == 'async' else 'online' if known.format == 'online' else 'in_person': True}
                   if known.format in {'async', 'online', 'in_person'} else {})
        if known.avoid_days:
            filters['avoid_days'] = known.avoid_days
        if known.earliest:
            filters['earliest'] = known.earliest
        if known.latest:
            filters['latest'] = known.latest
        args = {'text': text[:300], 'topic': '', 'term_code': term, 'requirements': [codes] if codes else None,
                'filters': filters, 'exclude': builder_selection or [], 'limit': 10}
    out = ctx.call('search_courses', args)
    if not out.ok:
        return AssistantReply(text='The course search could not run. Please try again.', generated_by='template')
    if not out.data.get('results'):
        return empty_reply(out)
    term = (out.data.get('interpretation') or {}).get('term_code') or args.get('term_code')
    f = args.get('filters') or {}
    fmt = 'async' if f.get('async_only') else 'online or async' if f.get('online') else 'in-person' if f.get('in_person') else 'course'
    return AssistantReply(text=f'Here are {len(out.data["results"])} {fmt} options for {term_label(term) or term} that fill remaining requirements. Nothing has been added.',
                          cards=[option_card(i, term_code=term, replaces=None) for i in out.data['results']],
                          generated_by='template', flow={'kind': 'course_search', 'args': args})
