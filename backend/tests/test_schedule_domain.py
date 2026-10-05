"""Schedule Engine building blocks (Phase 6.6) - pure, no database.

Meeting values imitate SOC rows (military times, H = Thursday). Cases with
no real Rutgers example in the archive say SYNTHETIC.
"""

from __future__ import annotations

import itertools
import random
from datetime import date

import pytest

from app.domain.schedule import MeetingKind, SchedulePreferences
from app.services.scheduling.meetings import (
    conflict,
    minutes,
    normalize,
    sections_conflict,
    time_verified,
    violates_window,
)
from app.services.scheduling.search import Candidate, incompatible_pairs, prefix, search


def M(day, start, end, *, s=None, e=None, mode="LEC", campus="BUS"):  # noqa: N802
    return normalize(day=day, start=start, end=end, mode_code="02", mode_desc=mode,
                     start_date=s, end_date=e, campus=campus)


# ==========================================================================
# normalization
# ==========================================================================


def test_military_times() -> None:
    assert minutes("1550") == 950 and minutes("0000") == 0 and minutes("2359") == 1439
    for bad in ("2400", "1260", "155", "ab12", "", None):
        assert minutes(bad) is None


@pytest.mark.parametrize(("mode", "kind"), [
    ("ONLINE INSTRUCTION(INTERNET)", MeetingKind.ASYNCHRONOUS),
    ("LEC", MeetingKind.TBA), ("RECIT", MeetingKind.TBA), ("LAB", MeetingKind.TBA),
    ("RSCH-MA", MeetingKind.ARRANGED), ("PROJ-IND", MeetingKind.ARRANGED),
    ("INTERNSP", MeetingKind.ARRANGED), ("MUS-INDV", MeetingKind.ARRANGED),
])
def test_meetings_without_a_time_are_classified_not_freed(mode, kind) -> None:
    m = normalize(day=None, start=None, end=None, mode_code="x", mode_desc=mode)
    assert m.kind is kind
    assert time_verified([m]) is (kind is MeetingKind.ASYNCHRONOUS)


def test_real_malformed_rows_are_never_intervals() -> None:
    # 07:966:333 publishes T 2330-1250; 07:966:123 publishes S 1100-1100.
    for day, s, e in (("T", "2330", "1250"), ("S", "1100", "1100"), ("X", "1000", "1100")):
        m = M(day, s, e)
        assert m.kind is MeetingKind.MALFORMED and not time_verified([m])
        assert m.raw["start_time_military"] == s


# ==========================================================================
# conflicts
# ==========================================================================


def test_overlap_boundaries() -> None:
    a = M("M", "1020", "1140")
    assert conflict(a, M("M", "1100", "1220"))                 # overlap
    assert conflict(a, M("M", "1020", "1140"))                 # identical
    assert conflict(a, M("M", "1030", "1100"))                 # contained
    assert not conflict(a, M("M", "1140", "1300"))             # back-to-back (SYNTHETIC)
    assert not conflict(M("M", "1140", "1300"), a)
    assert not conflict(a, M("T", "1020", "1140"))             # other day
    assert conflict(a, M("M", "1139", "1300"))                 # one minute


def test_buffer_is_the_students_own_minimum_break() -> None:
    a, b = M("M", "1020", "1140"), M("M", "1210", "1330")      # 30 minutes apart
    assert not conflict(a, b, buffer=0) and not conflict(a, b, buffer=30)
    assert conflict(a, b, buffer=31) and conflict(b, a, buffer=31)


def test_date_ranges() -> None:
    """Real Summer 2026 shapes: May 26 - Jul 2 vs Jul 6 - 31 never meet."""
    early = M("M", "1800", "2200", s=date(2026, 5, 26), e=date(2026, 7, 2))
    late = M("M", "1800", "2200", s=date(2026, 7, 6), e=date(2026, 7, 31))
    whole = M("M", "1800", "2200", s=date(2026, 5, 26), e=date(2026, 8, 12))
    partial = M("M", "1800", "2200", s=date(2026, 7, 1), e=date(2026, 7, 20))   # SYNTHETIC
    unknown = M("M", "1800", "2200")
    assert not conflict(early, late)
    assert conflict(early, whole) and conflict(late, whole)
    assert conflict(early, partial)                             # one shared day suffices
    assert conflict(early, unknown) and conflict(late, unknown)  # unknown dates: assume overlap


def test_only_timed_meetings_can_conflict() -> None:
    timed = M("M", "1020", "1140")
    for other in (normalize(day=None, start=None, end=None, mode_code="90",
                            mode_desc="ONLINE INSTRUCTION(INTERNET)"),
                  normalize(day=None, start=None, end=None, mode_code="02", mode_desc="LEC"),
                  M("M", "2330", "1250")):
        assert not conflict(timed, other)          # unknowns are flagged elsewhere, not freed


def test_every_meeting_of_both_sections_is_compared() -> None:
    """01:750:203 shape: T/F lecture + M recitation. The clash is the THIRD row."""
    s203 = [M("T", "1035", "1130"), M("F", "1035", "1130"), M("M", "1400", "1520")]
    s193 = [M("M", "1415", "1510"), M("W", "1415", "1510"), M("M", "1550", "1710")]
    clash = sections_conflict(s203, s193)
    assert clash is not None and clash[0].day == "M"
    assert not conflict(s203[0], s193[0])          # the first meetings alone do not clash


def test_hard_time_window() -> None:
    meetings = [M("M", "0830", "0950"), M("F", "1700", "1820")]
    assert violates_window(meetings, earliest=600, latest=None, avoid_days=[]) == "earliest_start"
    assert violates_window(meetings, earliest=None, latest=1080, avoid_days=[]) == "latest_end"
    assert violates_window(meetings, earliest=None, latest=None, avoid_days=["F"]) == "avoid_days"
    assert violates_window(meetings, earliest=500, latest=1200, avoid_days=["T"]) is None


def test_preferences_are_bounded_settings() -> None:
    with pytest.raises(ValueError):
        SchedulePreferences(avoid_days=["X"])
    with pytest.raises(ValueError):
        SchedulePreferences(earliest_start="25:00")
    with pytest.raises(ValueError):
        SchedulePreferences(minimum_minutes_between_classes=600)
    with pytest.raises(ValueError):
        SchedulePreferences(student_id="x")
    p = SchedulePreferences(avoid_days=["F", "M", "F"], preferred_campuses=[" bus ", "LIV"])
    assert p.avoid_days == ["M", "F"] and p.preferred_campuses == ["BUS", "LIV"]
    assert p.without("avoid_days").avoid_days == []


# ==========================================================================
# search
# ==========================================================================


def C(slot, index, *meetings, xl=(), weight=(0, 0, 0)):  # noqa: N802
    days = frozenset(m.day for m in meetings if m.kind is MeetingKind.TIMED)
    return Candidate(slot=slot, index=index, meetings=tuple(meetings),
                     cross_listed=frozenset(xl), weight=weight, days=days)


def _brute(domains, buffer=0):
    slots = sorted(domains)
    out = []
    for combo in itertools.product(*(domains[s] for s in slots)):
        ok = all(not sections_conflict(list(a.meetings), list(b.meetings), buffer)
                 and b.index not in a.cross_listed and a.index not in b.cross_listed
                 for a, b in itertools.combinations(combo, 2))
        if ok:
            out.append(frozenset(c.index for c in combo))
    return set(out)


def _random_domains(seed, courses=4, sections=6):
    rnd = random.Random(seed)
    starts = ["0830", "1020", "1210", "1400", "1550", "1740"]
    ends = {"0830": "0950", "1020": "1140", "1210": "1330", "1400": "1520", "1550": "1710",
            "1740": "1900"}
    domains, n = {}, 0
    for c in range(courses):
        slot = f"01:{100 + c}:101"
        cands = []
        for _ in range(sections):
            n += 1
            ms = []
            for day in rnd.sample("MTWHF", 2):
                st = rnd.choice(starts)
                ms.append(M(day, st, ends[st]))
            cands.append(C(slot, f"{10000 + n}", *ms))
        domains[slot] = cands
    return domains


@pytest.mark.parametrize("seed", range(8))
def test_search_finds_exactly_the_consistent_assignments(seed) -> None:
    domains = _random_domains(seed)
    found = search(domains, solution_limit=10**6)
    assert {frozenset(c.index for c in s) for s in found.solutions} == _brute(domains)
    assert found.nodes <= sum(len(v) for v in domains.values()) ** 2 * 50


@pytest.mark.parametrize("seed", range(6))
def test_branch_and_bound_returns_the_true_top_k(seed) -> None:
    domains = _random_domains(seed)

    def key(solution):
        return (*prefix(solution), tuple(sorted(c.index for c in solution)))

    every = search(domains, solution_limit=10**6).solutions
    expected = sorted(key(s) for s in every)[:5]
    bounded = search(domains, keep=5, full_key=key)
    assert [key(s) for s in bounded.solutions] == expected
    assert bounded.nodes <= search(domains, solution_limit=10**6).nodes


def test_search_order_does_not_depend_on_input_order() -> None:
    domains = _random_domains(3)
    reference = search(domains, solution_limit=10**6).solutions
    shuffled = {s: list(v) for s, v in reversed(list(domains.items()))}
    assert search(shuffled, solution_limit=10**6).solutions == reference


def test_cross_listed_partners_are_never_chosen_together() -> None:
    """SYNTHETIC times: asynchronous meetings, so ONLY the cross-list rule can
    separate them (real pair: 01:013:120 index 10052 <-> 01:074:120 10053)."""
    online = normalize(day=None, start=None, end=None, mode_code="90",
                       mode_desc="ONLINE INSTRUCTION(INTERNET)")
    domains = {"01:013:120": [C("01:013:120", "10052", online, xl=["10053"])],
               "01:074:120": [C("01:074:120", "10053", online, xl=["10052"])]}
    assert search(domains).solutions == []
    assert incompatible_pairs(domains) == [("01:013:120", "01:074:120")]


def test_limits_are_reported_not_hidden() -> None:
    domains = _random_domains(1, courses=5, sections=8)
    stopped = search(domains, node_limit=5, solution_limit=10**6)
    assert stopped.limit_reached
    capped = search(domains, solution_limit=1)
    assert len(capped.solutions) == 1


def test_an_empty_domain_ends_the_search_immediately() -> None:
    out = search({"A": [], "B": [C("B", "1", M("M", "1020", "1140"))]})
    assert out.solutions == [] and out.nodes == 0
