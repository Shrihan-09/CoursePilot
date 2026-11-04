"""Spread task: the cited degree credit total and how free electives spread."""

from __future__ import annotations

from decimal import Decimal as D

from app.services.planning.degree_total import SAS_2026_2027, degree_total, spread_electives


def test_the_sas_total_is_cited():
    total = degree_total("SAS", "2026-2027")
    assert total is SAS_2026_2027 and total.credits == D(120)
    assert total.quote.startswith("All students must earn at least 120 credits")
    assert total.url.startswith("https://newbrunswick-26-27-undergrad.catalogs.rutgers.edu/")
    assert len(total.archive_sha256) == 64 and total.retrieved_at == "2026-11-03"


def test_no_total_is_assumed_for_an_unread_school_or_year():
    assert degree_total("SAS", "2025-2026") is None
    assert degree_total("SOE", "2026-2027") is None


def test_course_terms_reach_their_floor_first():
    out = spread_electives([(D(13), D(16), D(19)), (D(16), D(16), D(19))], D(0), early=False)
    assert out == [D(3), D(0)]


def test_empty_terms_at_their_floor_then_the_rest_evenly():
    rows = [(D(16), D(16), D(19)), (D(0), D(16), D(19)), (D(0), D(16), D(19))]
    assert spread_electives(rows, D(20), early=False) == [D(2), D(18), D(0)]


def test_no_stranded_last_term_when_earlier_terms_have_room():
    rows = [(D(16), D(16), D(19)), (D(16), D(16), D(19)), (D(0), D(16), D(19))]
    assert spread_electives(rows, D(6), early=False) == [D(3), D(3), D(0)]


def test_a_last_term_below_its_floor_only_when_the_others_are_full():
    rows = [(D(19), D(16), D(19)), (D(0), D(16), D(19))]
    assert spread_electives(rows, D(6), early=False) == [D(0), D(6)]


def test_the_rest_spreads_evenly_up_to_the_caps():
    rows = [(D(16), D(16), D(19)), (D(0), D(16), D(19)), (D(0), D(16), D(19))]
    out = spread_electives(rows, D(36), early=False)
    assert sum(out) == D(36)
    totals = [c + e for (c, _, _), e in zip(rows, out, strict=True)]
    assert max(totals) - min(totals) <= 1 and max(totals) <= 19


def test_short_only_when_every_term_is_at_its_cap():
    rows = [(D(0), D(16), D(19)), (D(0), D(16), D(19))]
    assert spread_electives(rows, D(50), early=False) == [D(19), D(19)]


def test_early_fills_terms_to_their_cap_in_order():
    rows = [(D(16), D(16), D(19)), (D(0), D(16), D(19)), (D(0), D(16), D(19))]
    assert spread_electives(rows, D(25), early=True) == [D(3), D(19), D(3)]
