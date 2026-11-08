"""Search filters must describe one usable section, never a mix of sections."""
import pytest
from pydantic import ValidationError

from app.api.v1.routes.plan_changes import SearchRequest
from app.services.planning.course_search import Interpretation, _section_fits
from app.services.scheduling.meetings import normalize


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
