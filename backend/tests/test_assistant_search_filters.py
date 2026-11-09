"""No model/network: empty searches disclose filters and relax only on a tap."""
import json

import pytest

from app.services.assistant.agent import Assistant
from app.services.assistant.fake import FakeAssistantProvider
from app.services.assistant.eval.world import SyntheticTools


async def test_zero_search_always_explains_exact_filters_and_relaxation_is_opt_in():
    tools = SyntheticTools('alex')
    script = [json.dumps({'tool_calls': [{'name': 'search_courses', 'args': {
        'term_code': '20271', 'requirements': [['HST'], ['NS']], 'filters': {'async_only': True, 'open_only': True}, 'limit': 8}}]}),
        json.dumps({'reply': {'text': 'No results.'}})]
    assistant = Assistant(tools, default=FakeAssistantProvider('fake', script=script))
    before = tools.plan_revision()
    turn = await assistant.turn('Find options with these filters')
    assert 'Filters used:' in turn.reply.text
    assert 'AND' in turn.reply.text and 'async only' in turn.reply.text
    assert 'open sections only' in turn.reply.text
    assert turn.reply.questions and 'Relax one filter?' == turn.reply.questions[0].prompt
    assert tools.plan_revision() == before
    assert len([c for c in turn.tool_calls if c['name'] == 'search_courses']) == 1
    relaxed = await assistant.turn('', flow=turn.reply.flow, answers={'search_relax': 'format'})
    args = next(c['args'] for c in relaxed.tool_calls if c['name'] == 'search_courses')
    assert 'async_only' not in args.get('filters', {})
    assert args['filters']['open_only']  # do not relax unrelated constraints silently
    assert args['requirements'] == [['HST'], ['NS']]
    assert tools.plan_revision() == before


def test_drop_success_offers_only_opt_in_suggestions():
    from app.services.assistant.actions import _applied
    reply = _applied({'change': {'kind': 'remove', 'course': '01:082:105', 'term_code': '20271'},
                      'diff': {'summary': 'Dropped Art History'}}, {})
    assert any(c.label == 'Suggest a replacement' for c in reply.suggestions)
    assert reply.cards == [] and reply.proposals == []


def test_different_requirement_search_is_not_limited_to_three_question_chips():
    from app.services.assistant.guided import _open_requirements, _requirements
    progress = {'requirements': [{'code': f'R{i}', 'name': f'Requirement {i}', 'status': 'open'} for i in range(5)] +
                               [{'code': 'CORE_HST', 'name': 'Historical Analysis', 'status': 'planned'}]}
    opened = _open_requirements(progress, set())
    reqs, topic, _ = _requirements({'open': opened}, {'instead': 'different'})
    assert reqs == [[f'R{i}' for i in range(5)] + ['CORE_HST']]
    assert topic is None


async def test_builder_context_does_not_change_route_or_override_the_requested_term():
    tools = SyntheticTools('alex')
    assistant = Assistant(tools, default=FakeAssistantProvider())
    turn = await assistant.turn('Find async courses that fill a requirement', context_term='20279',
                               builder_selection=['01:198:211', '01:640:152'])
    assert turn.route.intent == 'question' and turn.usage.calls == 0
    args = next(c['args'] for c in turn.tool_calls if c['name'] == 'search_courses')
    assert args['term_code'] == '20279'
    explicit = await assistant.turn('Find async courses that fill a requirement in Spring 2027', context_term='20279')
    assert next(c['args'] for c in explicit.tool_calls if c['name'] == 'search_courses')['term_code'] == '20271'
