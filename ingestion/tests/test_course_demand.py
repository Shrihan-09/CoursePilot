"""Course demand collection (Phase 7.3) on real Rutgers data - SQLite.

Structure: the real Fall 2026 component fixtures. First availability: the
real 2026-10-14 openSections payload (01:750:203 had 2 of 41 sections open).
Every later change is a SYNTHETIC transition applied to a real section, and
every registration window here is SYNTHETIC (passed in, never the configured
Spring 2027 one), so the tests do not depend on today's date.

No test touches the network: the worker fetches through httpx.MockTransport.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from app.core import registration_windows as rw
from app.models import (
    Course,
    CourseDemandSnapshot,
    CourseOffering,
    CourseSection,
    SectionAvailability,
    SectionAvailabilityChange,
)
from app.models.availability import AvailabilityRefresh
from app.models.live import AvailabilityPollSchedule
from app.services.demand import day_bounds, due_days, eastern_day, rollup_day
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from coursepilot_ingestion.live_worker import LiveWorker

from .test_live_availability import FALL26, T0, _payload, _refresh, world  # noqa: F401

CALC = "01:750:203"
R1 = T0 + timedelta(hours=3)          # 2026-10-14 01:29 Eastern
OPENS = T0 + timedelta(hours=4)       # SYNTHETIC window opens 02:29 Eastern
R2 = T0 + timedelta(hours=5)          # 03:29 Eastern
DAY = date(2026, 10, 14)
NOW = day_bounds(DAY)[1] + timedelta(hours=1)


def _window(opens=OPENS, closes=OPENS + timedelta(days=10)):
    return (rw.RegistrationWindow(FALL26, opens, closes, "SYNTHETIC", "synthetic://test"),)


def _indexes(s, course_string):
    return sorted(s.scalars(
        select(CourseSection.index_number)
        .join(CourseOffering, CourseOffering.id == CourseSection.offering_id)
        .join(Course, Course.id == CourseOffering.course_id)
        .where(Course.course_string == course_string, CourseSection.term_code == FALL26)))


def _open_now(s, indexes):
    status = dict(s.execute(select(SectionAvailability.index_number, SectionAvailability.status)
                            .where(SectionAvailability.term_code == FALL26)).all())
    return [i for i in indexes if status.get(i) == "open"]


def _snapshot(s, course_string, day=DAY):
    return s.scalar(select(CourseDemandSnapshot).where(
        CourseDemandSnapshot.course_string == course_string,
        CourseDemandSnapshot.snapshot_date == day))


def _values(s):
    skip = {"id", "created_at", "updated_at"}
    return sorted(tuple((c.name, getattr(r, c.key)) for c in CourseDemandSnapshot.__table__.c
                        if c.name not in skip)
                  for r in s.scalars(select(CourseDemandSnapshot)))


@pytest.fixture
def observed(world):  # noqa: F811
    """R1: the real payload; R2 (after the window opens): one of 01:750:203's two
    open sections SYNTHETICALLY closes."""
    assert _refresh(world, at=R1).verdict == "accepted"
    sections = _indexes(world, CALC)
    opened = _open_now(world, sections)
    assert (len(sections), len(opened)) == (41, 2)             # real: 2 of 41 open
    assert _refresh(world, [i for i in _payload() if i != opened[0]],
                    at=R2).verdict == "accepted"
    world.commit()
    return world, sections, opened


# ==========================================================================
# rollup math
# ==========================================================================


def test_closed_share_counts_and_first_closed_times(observed) -> None:
    s, sections, (closed_at_r2, still_open) = observed
    rollup_day(s, FALL26, DAY, now=NOW, windows=_window())
    row = _snapshot(s, CALC)
    assert row.status == "ok" and row.insufficient_reason is None
    assert (row.section_count, row.observed_count, row.open_count, row.closed_count) == (
        41, 41, 1, 40)
    assert row.closed_share == Decimal("0.9756")               # 40 / 41
    assert row.refreshes == 2
    assert row.first_closed[closed_at_r2] == {
        "kind": "closed_after_window", "observed_at": R2.isoformat(),
        "not_before": R1.isoformat()}                          # closed in (R1, R2]
    assert still_open not in row.first_closed                  # never closed: no time
    before = set(sections) - {closed_at_r2, still_open}
    assert {row.first_closed[i]["kind"] for i in before} == {"closed_before_window"}
    assert len(row.first_closed) == 40
    assert row.window_opens_at.replace(tzinfo=UTC) == OPENS


def test_a_section_first_seen_closed_after_the_window_opened_has_no_time(observed) -> None:
    s, _, (closed_at_r2, _) = observed
    rollup_day(s, FALL26, DAY, now=NOW, windows=_window(opens=R1 - timedelta(minutes=1)))
    row = _snapshot(s, CALC)
    kinds = {i: v["kind"] for i, v in row.first_closed.items()}
    assert kinds[closed_at_r2] == "closed_after_window"
    others = [k for i, k in kinds.items() if i != closed_at_r2]
    assert set(others) == {"closed_when_first_observed"}       # not imputed to R1
    assert all(row.first_closed[i]["observed_at"] is None for i in kinds if i != closed_at_r2)


def test_before_the_window_opens_there_is_no_first_closed_data(observed) -> None:
    s, *_ = observed
    rollup_day(s, FALL26, DAY, now=NOW, windows=_window(opens=NOW + timedelta(days=5)))
    row = _snapshot(s, CALC)
    assert row.status == "ok" and row.first_closed == {}
    assert row.closed_share == Decimal("0.9756")


def test_a_term_without_a_configured_window_records_shares_only(observed) -> None:
    s, *_ = observed
    rollup_day(s, FALL26, DAY, now=NOW, windows=())
    row = _snapshot(s, CALC)
    assert row.window_opens_at is None and row.first_closed == {}


def test_a_day_without_an_accepted_refresh_is_insufficient_never_imputed(observed) -> None:
    s, *_ = observed
    later = DAY + timedelta(days=1)                            # nothing polled that day
    report = rollup_day(s, FALL26, later, now=NOW + timedelta(days=1), windows=_window())
    assert report.ok == 0 and report.reasons == {"no_accepted_refresh_that_day": report.insufficient}
    row = _snapshot(s, CALC, later)
    assert row.status == "insufficient_data"
    assert (row.observed_count, row.open_count, row.closed_count, row.closed_share) == (
        None, None, None, None)                               # yesterday's state NOT carried
    assert row.refreshes == 0 and row.max_refresh_gap_seconds is None
    assert row.first_closed == {}


def test_a_course_with_no_observed_section_is_insufficient(observed) -> None:
    s, *_ = observed
    # SYNTHETIC: an accepted refresh for Fall 2025 that observed nothing.
    s.add(AvailabilityRefresh(term_code="20259", source_url="synthetic://test", observed_at=R1,
                              verdict="accepted", matched=0, transitions=0))
    s.commit()
    report = rollup_day(s, "20259", DAY, now=NOW, windows=())
    assert report.ok == 0 and report.reasons == {"no_section_observed": report.insufficient}
    assert report.insufficient > 0


def test_the_refresh_gap_is_recorded(observed) -> None:
    s, *_ = observed
    rollup_day(s, FALL26, DAY, now=NOW, windows=_window())
    start, end = day_bounds(DAY)
    longest = max(R1 - start, R2 - R1, end - R2)
    assert _snapshot(s, CALC).max_refresh_gap_seconds == int(longest.total_seconds())


def test_days_are_eastern_and_dst_correct() -> None:
    assert eastern_day(T0) == date(2026, 10, 13)               # 22:29 EDT
    start, end = day_bounds(date(2026, 11, 1))                 # DST ends: a 25-hour day
    assert end - start == timedelta(hours=25)
    assert start == datetime(2026, 11, 1, 4, tzinfo=UTC)


# ==========================================================================
# idempotent writes
# ==========================================================================


def test_the_same_payload_twice_writes_no_change_rows(observed) -> None:
    s, *_ = observed
    changes = s.scalar(select(func.count()).select_from(SectionAvailabilityChange))
    again = _refresh(s, [i for i in _payload() if i != observed[2][0]], at=R2 + timedelta(minutes=2))
    assert again.verdict == "accepted" and again.transitions == 0
    assert s.scalar(select(func.count()).select_from(SectionAvailabilityChange)) == changes


def test_rolling_up_a_day_twice_gives_identical_rows(observed) -> None:
    s, *_ = observed
    rollup_day(s, FALL26, DAY, now=NOW, windows=_window())
    s.commit()
    first = _values(s)
    rollup_day(s, FALL26, DAY, now=NOW, windows=_window())
    s.commit()
    assert _values(s) == first
    assert s.scalar(select(func.count()).select_from(CourseDemandSnapshot)) == len(first)


def test_due_days_skips_done_days_and_today(observed) -> None:
    s, *_ = observed
    assert due_days(s, FALL26, now=NOW) == [DAY]
    rollup_day(s, FALL26, DAY, now=NOW, windows=_window())
    s.commit()
    assert due_days(s, FALL26, now=NOW) == []
    assert due_days(s, FALL26, now=R2) == []                   # DAY is not over yet


# ==========================================================================
# cadence and request budget
# ==========================================================================


def test_poll_interval_is_90s_in_a_window_and_30_minutes_outside() -> None:
    windows = _window()
    assert rw.poll_interval(FALL26, 90, OPENS, windows) == timedelta(seconds=90)
    assert rw.poll_interval(FALL26, 90, OPENS - timedelta(seconds=1), windows) == timedelta(
        minutes=30)
    assert rw.poll_interval("20271", 90, OPENS, windows) == timedelta(minutes=30)  # other term
    assert rw.poll_interval(FALL26, 3600, OPENS - timedelta(days=1), windows) == timedelta(
        hours=1)                                               # a slower operator setting wins


def test_spring_2027_window_is_the_registrar_schedule() -> None:
    [w] = [x for x in rw.REGISTRATION_WINDOWS if x.term_code == "20271"]
    assert w.opens_at == datetime(2026, 11, 9, 9, tzinfo=rw.EASTERN)
    assert w.closes_at == datetime(2026, 11, 20, 2, tzinfo=rw.EASTERN)
    assert w.source_url == "https://nbregistrar.rutgers.edu/undergrad/S27prereg.htm"


def test_one_request_per_term_per_poll_and_the_rollup_makes_none(world, tmp_path,  # noqa: F811
                                                                 monkeypatch) -> None:
    """Two simulated hours, a tick every 30 s: hour 1 inside a SYNTHETIC window,
    hour 2 outside. The clock crosses Eastern midnight, so the hourly demand
    step rolls up a day during the run."""
    start = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)           # Oct 13, 23:00 Eastern
    monkeypatch.setattr(rw, "REGISTRATION_WINDOWS",
                        _window(opens=start, closes=start + timedelta(hours=1)))
    world.add(AvailabilityPollSchedule(term_code=FALL26, interval_seconds=90,
                                       backoff_cap_seconds=900, active=True))
    world.commit()
    calls = []

    def rutgers(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=json.dumps(_payload()).encode())

    now = [start]
    worker = LiveWorker(sessionmaker(world.get_bind(), expire_on_commit=False), owner="w1",
                        archive_dir=tmp_path, transport=httpx.MockTransport(rutgers),
                        clock=lambda: now[0])
    rolled = 0
    for step in range(240):                                    # 2 h at 30 s
        now[0] = start + timedelta(seconds=30 * step)
        report = worker.tick()
        assert not report.errors
        rolled += report.demand_days
        if step == 119:
            in_window = len(calls)
    assert in_window == 40                                     # 3600 s / 90 s, one each
    assert len(calls) - in_window == 2                         # 30-minute cadence outside
    assert all("openSections.json" in u and "term=9" in u and "year=2026" in u for u in calls)
    assert rolled == 1                                         # Oct 13, from data alone
    assert world.scalar(select(func.count()).select_from(CourseDemandSnapshot)) > 0
