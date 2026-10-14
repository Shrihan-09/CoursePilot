"""Registration availability rules (Phase 6.7) - pure, offline, no database.

Payload shapes are the real ones measured in
docs/investigations/phase-6-7-live-availability.md: openSections.json is a
JSON array of 5-digit index strings, term-wide across campuses, with no
timestamp, and `[]` for an unpublished term, a nonsense year or campus.
Inputs not seen from Rutgers are labelled SYNTHETIC.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.availability import (
    CONFLICT_WINDOW,
    CURRENT_WINDOW,
    AvailabilityStatus,
    Freshness,
    MalformedSource,
    Observation,
    RefreshVerdict,
    bundle_status,
    decide_refresh,
    oldest,
    parse_open_sections,
    resolve,
    term_of_url,
    transitions,
    weakest,
)

OPEN, CLOSED, UNKNOWN = (AvailabilityStatus.OPEN, AvailabilityStatus.CLOSED,
                        AvailabilityStatus.UNKNOWN)
T0 = datetime(2026, 10, 14, 2, 29, 51, tzinfo=UTC)       # the real Fall 2026 fetch
TERM = ["13370", "13372", "13384", "13523", "13551"]      # real 01:750 Fall 2026 indexes


def _body(entries) -> bytes:
    return json.dumps(entries).encode()


# ---------------------------------------------------------------- parsing


def test_parses_the_published_shape() -> None:
    payload = parse_open_sections(b'["13384","13551","05123"]')
    assert payload.indexes == {"13384", "13551", "05123"}          # leading zero kept
    assert (payload.entries, payload.duplicates) == (3, 0)


def test_duplicate_indexes_are_counted_not_doubled() -> None:     # SYNTHETIC
    payload = parse_open_sections(_body(["13384", "13384", "13551"]))
    assert payload.indexes == {"13384", "13551"} and payload.duplicates == 1


@pytest.mark.parametrize("body", [
    b"<html>Service Unavailable</html>",               # an error page
    b'{"error": "bad term"}',                          # an object, not a list
    b'[13384, 13551]',                                 # numbers: leading zeros would be lost
    b'["13384", null]',
    b'["1338"]',                                       # 4 digits
    b'["13384 "]',                                     # whitespace
    b'["ABCDE"]',
    b"\xff\xfe",
    b"",
])
def test_malformed_payloads_are_rejected(body) -> None:           # SYNTHETIC shapes
    with pytest.raises(MalformedSource):
        parse_open_sections(body)


def test_parsing_is_deterministic() -> None:
    body = _body(["13551", "13384", "13523"])
    assert parse_open_sections(body) == parse_open_sections(body)
    assert decide_refresh(parse_open_sections(body), TERM) == decide_refresh(
        parse_open_sections(_body(["13523", "13384", "13551"])), list(reversed(TERM)))


# ---------------------------------------------------------------- one refresh


def test_listed_is_open_unlisted_known_section_is_closed() -> None:
    d = decide_refresh(parse_open_sections(_body(["13384", "13551", "99999"])), TERM)
    assert d.verdict is RefreshVerdict.ACCEPTED
    assert d.statuses == {"13370": CLOSED, "13372": CLOSED, "13384": OPEN, "13523": CLOSED,
                          "13551": OPEN}
    assert (d.matched, d.ignored) == (2, 1)                       # 99999: another campus


def test_an_index_missing_from_the_term_is_ignored_never_invented() -> None:
    d = decide_refresh(parse_open_sections(_body(["13384", "77777"])), TERM)
    assert "77777" not in d.statuses and d.ignored == 1


def test_empty_payload_never_closes_every_section() -> None:
    """Real: `[]` for unpublished Spring 2027, year 1999 and campus XX alike."""
    d = decide_refresh(parse_open_sections(b"[]"), TERM)
    assert d.verdict is RefreshVerdict.EMPTY and d.statuses == {}


def test_a_payload_sharing_nothing_with_the_term_is_rejected() -> None:
    d = decide_refresh(parse_open_sections(_body(["40001", "40002"])), TERM)  # SYNTHETIC
    assert d.verdict is RefreshVerdict.NO_OVERLAP and d.statuses == {}


def test_wrong_campus_is_the_same_payload() -> None:
    """Real: campus=NK returned NB's bytes; other campuses' indexes are just
    not sections of this term's CoursePilot data, so they are ignored."""
    nb = decide_refresh(parse_open_sections(_body(["13384", "26001"])), TERM)
    nk = decide_refresh(parse_open_sections(_body(["13384", "26001"])), TERM)
    assert nb == nk and nb.ignored == 1


def test_an_implausible_collapse_is_rejected() -> None:            # SYNTHETIC counts
    term = [f"{i:05d}" for i in range(1000)]
    few = parse_open_sections(_body([f"{i:05d}" for i in range(5)]))
    assert decide_refresh(few, term, previous_open=500).verdict is RefreshVerdict.IMPLAUSIBLE_DROP
    assert decide_refresh(few, term, previous_open=None).verdict is RefreshVerdict.ACCEPTED
    # a small term (previous < 100) is never judged by the ratio
    assert decide_refresh(few, term, previous_open=40).verdict is RefreshVerdict.ACCEPTED
    ok = parse_open_sections(_body([f"{i:05d}" for i in range(60)]))
    assert decide_refresh(ok, term, previous_open=500).verdict is RefreshVerdict.ACCEPTED


def test_the_request_url_names_the_term() -> None:
    url = "https://classes.rutgers.edu/soc/api/openSections.json?year=2026&term=9&campus=NB"
    assert term_of_url(url) == "20269"
    assert term_of_url("file:///archive/openSections.json") is None


# ---------------------------------------------------------------- change detection


def test_open_to_open_is_not_an_event() -> None:
    assert transitions({"13384": OPEN}, {"13384": OPEN}) == []


@pytest.mark.parametrize(("before", "after"), [(CLOSED, OPEN), (OPEN, CLOSED), (UNKNOWN, OPEN)])
def test_state_changes_are_transitions(before, after) -> None:
    [t] = transitions({"13384": before}, {"13384": after})
    assert (t.index, t.from_status, t.to_status) == ("13384", before, after)


def test_first_observation_has_no_previous_state() -> None:
    [t] = transitions({}, {"13384": CLOSED})
    assert t.from_status is None and t.to_status is CLOSED


def test_transitions_are_ordered_and_only_changes() -> None:
    before = {"13551": OPEN, "13384": CLOSED, "13370": CLOSED}
    after = {"13551": CLOSED, "13384": OPEN, "13370": CLOSED}
    assert [t.index for t in transitions(before, after)] == ["13384", "13551"]


# ---------------------------------------------------------------- resolving two sources


def _obs(status, at, source="openSections.json", current=True):
    return Observation(status, at, source, can_be_current=current)


def test_no_observation_is_unknown_and_unobserved() -> None:
    r = resolve([], T0)
    assert (r.status, r.freshness, r.reasons) == (UNKNOWN, Freshness.UNOBSERVED, ("not_observed",))


def test_a_recent_refresh_is_current_an_old_one_stale() -> None:
    assert resolve([_obs(OPEN, T0)], T0 + CURRENT_WINDOW).freshness is Freshness.CURRENT
    stale = resolve([_obs(OPEN, T0)], T0 + CURRENT_WINDOW + timedelta(seconds=1))
    assert (stale.status, stale.freshness) == (OPEN, Freshness.STALE)   # kept, labelled


def test_a_structural_download_is_never_current() -> None:
    """courses.json's time is an archive FILE time - a copy resets it."""
    r = resolve([_obs(OPEN, T0, "courses.json", current=False)], T0)
    assert (r.status, r.freshness) == (OPEN, Freshness.STALE)
    assert r.reasons == ("structural_snapshot_not_an_availability_refresh",)


def test_the_newest_observation_wins_outside_the_conflict_window() -> None:
    """Real drift: 01:750:194 LB 13378 was open in the 2026-09-08 download,
    closed in the 2026-10-14 openSections payload."""
    old = _obs(OPEN, datetime(2026, 9, 8, tzinfo=UTC), "courses.json", current=False)
    r = resolve([old, _obs(CLOSED, T0)], T0 + timedelta(minutes=1), since=T0)
    assert (r.status, r.freshness, r.source, r.since) == (CLOSED, Freshness.CURRENT,
                                                          "openSections.json", T0)


def test_sources_that_disagree_at_the_same_time_make_unknown() -> None:   # SYNTHETIC timing
    a = _obs(OPEN, T0 - CONFLICT_WINDOW + timedelta(seconds=1), "courses.json", current=False)
    r = resolve([a, _obs(CLOSED, T0)], T0)
    assert r.status is UNKNOWN
    assert r.reasons == ("sources_disagree:courses.json=open,openSections.json=closed",)


def test_resolution_is_input_order_free() -> None:
    a = _obs(OPEN, T0 - timedelta(hours=2), "courses.json", current=False)
    b = _obs(CLOSED, T0)
    assert resolve([a, b], T0) == resolve([b, a], T0)


# ---------------------------------------------------------------- bundles


@pytest.mark.parametrize(("components", "expected"), [
    ([OPEN], OPEN),                              # A: ordinary course
    ([OPEN, OPEN], OPEN),                        # B: lecture + LB lab
    ([OPEN, CLOSED], CLOSED),                    # C: lab closed
    ([CLOSED, OPEN], CLOSED),                    # D: lecture closed
    ([OPEN, UNKNOWN], UNKNOWN),                  # E: lab unknown
    ([CLOSED, UNKNOWN], CLOSED),                 # closed dominates unknown
    ([], UNKNOWN),                               # nothing to judge
])
def test_bundle_rule(components, expected) -> None:
    assert bundle_status(components) is expected


def test_a_bundle_is_as_fresh_as_its_least_fresh_component() -> None:
    assert weakest([Freshness.CURRENT, Freshness.STALE]) is Freshness.STALE
    assert weakest([Freshness.CURRENT, Freshness.UNOBSERVED]) is Freshness.UNOBSERVED
    assert weakest([Freshness.CURRENT]) is Freshness.CURRENT
    assert weakest([]) is Freshness.UNOBSERVED
    assert oldest([T0, None, T0 - timedelta(days=1)]) == T0 - timedelta(days=1)
    assert oldest([None]) is None
