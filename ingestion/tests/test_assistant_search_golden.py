"""Real archived Spring 2027 SOC; fake provider; fabricated record; no external calls."""
import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Course, StudentCourse
from app.services.assistant.agent import Assistant
from app.services.assistant.db_tools import DbAssistantTools
from app.services.assistant.fake import FakeAssistantProvider
from app.services.assistant.eval.golden import REAL_DATA_SEARCH_CASES
from app.services.assistant.tools import run_tool
from app.services.planning.edits import profile_constraints
from app.services.planning.saved import save_plan
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.pipelines.core import CoreIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery
from .conftest import FIXTURE_DIR, SAS_CORE
from .test_plan_options import SPRING27, _saved, _row, today, world

EXPECTED = {'37:575:202', '01:840:305', '01:355:201', '04:189:152'}


@pytest.fixture
def async_world(world, today, tmp_path):
    session = world
    student = _saved(session)
    row = _row(session, student)
    original = {t.term_code: [c.course for c in t.courses] for t in save_plan(
        session, student, start_term=SPRING27, constraints=profile_constraints(session, student)).result.terms}
    raw = (FIXTURE_DIR / 'soc_async_requirements_20271.json').read_bytes()
    (tmp_path / 'soc_courses_2027_1_NB.json').write_bytes(raw)
    query = SocQuery(year=2027, term='1', campus='NB')
    CourseIngestionPipeline(session, tmp_path).run(query, coverage='partial')
    SectionIngestionPipeline(session, tmp_path).run(query)
    base = json.loads((FIXTURE_DIR / 'soc_workload_terms_sample.json').read_bytes())
    combined = {r['courseString']: r for records in base.values() for r in records}
    combined.update({r['courseString']: r for r in json.loads(raw)})
    all_records = tmp_path / 'combined-core.json'
    all_records.write_text(json.dumps(list(combined.values())), encoding='utf-8')
    CoreIngestionPipeline(session).run(SAS_CORE, all_records, observed_term_code=SPRING27)
    writing = session.scalar(select(Course).where(Course.course_string == '01:355:101'))
    session.add(StudentCourse(student_id=student.id, course_id=writing.id, term_code='20261',
                              status='completed', grade='A', credits_earned=writing.credits))
    # Preserve the existing first-term picks, so all four alternatives remain
    # searchable (unselected now, or movable from a later term).
    row.edits = {'term_rosters': {SPRING27: original[SPRING27]}}
    session.commit()
    save_plan(session, student, start_term=SPRING27, constraints=profile_constraints(session, student))
    session.commit()
    return session, student


@pytest.mark.parametrize('case', REAL_DATA_SEARCH_CASES, ids=lambda c: c['id'])
def test_async_requirement_golden_returns_known_official_courses(async_world, case):
    session, student = async_world
    tools = DbAssistantTools(session, student)
    before = tools.plan_revision()
    turn = asyncio.run(Assistant(tools, default=FakeAssistantProvider('fake')).turn(case['text']))
    cards = {c.course: c for c in turn.reply.cards}
    assert EXPECTED <= cards.keys(), (turn.reply.text, turn.tool_calls, list(cards))
    assert all('async' in c.delivery or 'asynchronous' in c.delivery for c in cards.values())
    assert tools.plan_revision() == before and not turn.reply.undo_available
    assert turn.reply.proposals == []
    search = next(c['args'] for c in turn.tool_calls if c['name'] == 'search_courses')
    assert len(search['requirements']) == 1  # alternatives, not accidental AND
    assert search['filters'] == {'async_only': True}
    assert search['term_code'] == SPRING27 and not search.get('topic')
    assert turn.reply.searches and turn.reply.searches[0]['args'] == search


def test_exact_adapter_arguments_distinguish_and_or_and_asynch(async_world):
    session, student = async_world
    tools = DbAssistantTools(session, student)
    reqs = ['CORE_HST', 'CORE_WCR', 'CORE_NS', 'CS_ELECTIVES', 'CORE_WCD']
    common = {'term_code': SPRING27, 'filters': {'async_only': True}, 'limit': 10}
    intersection = run_tool(tools, 'search_courses', common | {'requirements': [[r] for r in reqs[:4]]}, gpa_allowed=False)
    alternatives = run_tool(tools, 'search_courses', common | {'requirements': [reqs]}, gpa_allowed=False)
    assert not intersection.data['results']
    assert EXPECTED <= {r['course'] for r in alternatives.data['results']}
    direct = tools.search_courses(text='asynch', term_code=SPRING27, requirements=[['CORE_WCD']],
                                  topic=None, filters=None, exclude=[], limit=10)
    assert EXPECTED <= {r['course'] for r in direct['results']}
