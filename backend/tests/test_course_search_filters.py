"""Search filters must describe one usable section, never a mix of sections."""
import pytest
from pydantic import ValidationError

from app.api.v1.routes.plan_changes import SearchRequest
from app.services.planning.course_search import Interpretation, _section_fits
from app.services.scheduling.meetings import normalize


def test_requirement_search_checks_late_sorting_courses_before_applying_delivery(monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace as NS
    from app.services.planning import course_search as search
    from app.domain.planning import OfferingEvidenceKind
    target = '37:575:202'
    keys = [f'01:999:{n:03}' for n in range(100, 400)] + [target]
    checked = []
    ev = NS(kind=OfferingEvidenceKind.CONFIRMED_IN_TERM)
    engine = NS(pools={'CORE_HST': keys}, pool_categories={}, offerings=NS(evidence=lambda *args: ev),
                course_info={target: NS(title='Test late-sorting course', credits=Decimal(3))}, attributes=None,
                _usable=lambda codes: 1)
    ctx = NS(engine=engine, open=['CORE_HST'], where={}, session=None, liked=[],
             names={'CORE_HST': 'Historical Analysis'}, fills=lambda key: ['CORE_HST'],
             c=NS(honors=False, filler_preference=NS(value='flexible')))
    def check(term, candidates, **kwargs):
        checked.extend(candidates)
        return {k: NS(course=k, ok=k == target, reason='prerequisites_not_met', requirements=['CORE_HST'],
                      offering=ev, workload=NS(delivery=['async'], weight=Decimal(3), contact_hours=Decimal(0))) for k in candidates}
    ctx.check = check
    monkeypatch.setattr(search, '_sections', lambda *args: {target: [('99999', 'open', [])]})
    monkeypatch.setattr(search, '_blocked', lambda *args: [])
    monkeypatch.setattr(search, '_section_fits', lambda *args: True)
    monkeypatch.setattr(search, 'Similarity', lambda *args: NS(best=lambda *args: (0, None)))
    out = search._search(ctx, search.Interpretation(term_code='20271', async_only=True), 10)
    assert len(checked) == 301
    assert [r.course for r in out.results] == [target]


def meeting(day="M", start="1000", end="1120", campus="CAC", mode="LEC"):
    return normalize(day=day, start=start, end=end, campus=campus,
                     mode_code=None, mode_desc=mode)


def test_filters_must_fit_the_same_section():
    # The online section meets Friday; the Monday section is in person.
    # Neither satisfies BOTH online and Fridays off.
    request = Interpretation(term_code="20271", online=True, avoid_days=["F"])
    assert not _section_fits([meeting("F", campus="ONL", mode="ONLINE INSTRUCTION")], request, [])
    assert not _section_fits([meeting("M")], request, [])
    assert _section_fits([meeting("M", campus="ONL", mode="ONLINE INSTRUCTION")], request, [])


def test_campus_and_time_filters_respect_known_meetings_and_blocked_times():
    request = Interpretation(term_code="20271", campus="CAC", earliest="10:00", latest="12:00")
    assert _section_fits([meeting()], request, [])
    assert not _section_fits([meeting(campus="BUS")], request, [])
    assert not _section_fits([meeting(start="0900")], request, [])
    assert not _section_fits([meeting()], request, [("M", 600, 660)])
    assert not _section_fits([meeting(day=None, start=None, end=None)], request, [])


@pytest.mark.parametrize("filters", [{"earliest": "25:00"}, {"avoid_days": ["Friday"]},
                                     {"level": 5}, {"credits": -3}])
def test_search_filters_are_typed(filters):
    with pytest.raises(ValidationError):
        SearchRequest(filters=filters)


def test_search_accepts_combined_structured_filters():
    request = SearchRequest(text="writing", filters={"campus": "CAC", "earliest": "10:00",
                            "latest": "15:00", "open_only": True, "avoid_days": ["F"]})
    assert request.filters.open_only and request.filters.campus == "CAC"


def test_named_goal_needs_that_goal_in_the_offering_listing():
    from types import SimpleNamespace as NS
    from app.services.planning.course_search import _goal_filters_match

    ctx = NS(engine=NS(_core_evidence=lambda *args: NS(codes=["AHp"])))
    verdict = NS(course="01:999:101", offering=object())
    request = Interpretation(term_code="20271", requirements=[["CORE_AH#AHo"]])
    assert not _goal_filters_match(ctx, verdict, request)
    request.requirements = [["CORE_AH#AHp"]]
    assert _goal_filters_match(ctx, verdict, request)


def test_a_known_requirement_with_no_loaded_pool_never_becomes_generic_search():
    from types import SimpleNamespace as NS
    from app.services.planning.course_search import interpret

    engine = NS(pool_categories={}, requirements={}, pools={}, by_id_code={})
    ctx = NS(engine=engine, open=[], inactive=set(), names={}, plan=NS(terms=[NS(term_code="20271")]))
    result = interpret(ctx, "a writing course that also fills diversity")
    assert result.requirements == [[], []]
    assert set(result.requirement_names) == {"writing", "diversity"}
    assert result.topic is None


def test_aho_is_a_goal_even_when_the_loaded_pool_contains_only_another_ah_goal():
    from types import SimpleNamespace as NS
    from app.services.planning.course_search import interpret

    engine = NS(pool_categories={("CORE_AH", "01:999:101"): {"AHp"}},
                requirements={"CORE_AH": NS(name="Arts and the Humanities")},
                pools={"CORE_AH": {"01:999:101"}}, by_id_code={})
    ctx = NS(engine=engine, open=["CORE_AH"], inactive=set(), names={"CORE_AH": "Arts"},
             plan=NS(terms=[NS(term_code="20271")]))
    result = interpret(ctx, "a different AHo course in Spring 2027, online")
    assert result.requirements == [["CORE_AH#AHo"]]
    assert result.topic is None and result.online
