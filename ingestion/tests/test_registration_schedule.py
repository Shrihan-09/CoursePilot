"""Spring 2027 registration schedule: credit tiers, the nightly WebReg closure.

Source: an owner-provided screenshot of
https://nbregistrar.rutgers.edu/undergrad/s27prereg.htm (recorded 2026-11-08),
stored in backend/app/core/data/registration_schedule_20271.json.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core import registration_windows as rw
from app.core.registration_windows import (
    EASTERN,
    CreditTier,
    RegistrationSchedule,
    in_nightly_closure,
    poll_interval,
    registration_phase,
    registration_schedule,
)
from app.services.demand import changes_by_registration_phase

from .test_live_availability import FALL26, T0, _payload, _refresh, world  # noqa: F401

SPRING27 = "20271"


def _et(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=EASTERN)


def test_the_spring_2027_schedule_is_on_file_with_its_source():
    s = registration_schedule(SPRING27)
    assert s.source_url == "https://nbregistrar.rutgers.edu/undergrad/s27prereg.htm"
    assert "screenshot" in s.source_basis
    assert [t.min_degree_credits for t in s.tiers] == [105, 90, 75, 60, 40, 25, 1, 0]
    assert [t.opens_at.day for t in s.tiers] == [9, 10, 11, 12, 13, 16, 17, 18]
    assert all(t.opens_at.hour == 9 and t.opens_at.month == 11 for t in s.tiers)
    assert s.all_students_opens_at == _et("2026-11-19T09:00")
    assert (s.closure_start.hour, s.closure_end.hour) == (2, 6)
    assert s.tier_for_credits(47).min_degree_credits == 40
    assert s.tier_for_credits(0).min_degree_credits == 0
    assert s.tier_for_credits(105).min_degree_credits == 105


@pytest.mark.parametrize(("moment", "phase"), [
    ("2026-11-09T08:59", "before registration"),
    ("2026-11-09T09:00", "105+ degree credits"),
    ("2026-11-12T13:00", "60+ degree credits"),
    ("2026-11-17T10:00", "1+ degree credits (001)"),
    ("2026-11-18T23:00", "0 degree credits"),
    ("2026-11-19T09:00", "all students"),
    ("2026-11-14T03:30", "nightly closure"),
])
def test_the_phase_at_any_moment_is_computed_from_the_timestamp(moment, phase):
    assert registration_phase(SPRING27, _et(moment)).label == phase


def test_no_schedule_no_phase():
    assert registration_phase("20279", _et("2026-11-12T13:00")) is None
    assert in_nightly_closure("20279", _et("2026-11-12T03:00")) is False


def test_polling_slows_to_30_minutes_during_the_nightly_closure():
    inside = poll_interval(SPRING27, 90, _et("2026-11-12T13:00"))
    closed = poll_interval(SPRING27, 90, _et("2026-11-12T03:15"))
    reopened = poll_interval(SPRING27, 90, _et("2026-11-12T06:00"))
    assert inside == timedelta(seconds=rw.WINDOW_INTERVAL_SECONDS)
    assert closed == timedelta(seconds=rw.OUTSIDE_INTERVAL_SECONDS)
    assert reopened == inside
    # Outside the registration window nothing changes.
    assert poll_interval(SPRING27, 90, _et("2026-12-01T03:15")) == closed


def test_recorded_status_changes_are_tagged_with_the_tier_after_the_fact(world):  # noqa: F811
    """A SYNTHETIC schedule for Fall 2026 around the archived fetch (T0 =
    2026-10-14 02:29 UTC): the first observation falls before registration, a
    synthetic close two hours later inside the 90+ tier."""
    opens = T0 + timedelta(hours=1)
    sched = {FALL26: RegistrationSchedule(
        term_code=FALL26, term_label="Fall 2026",
        tiers=(CreditTier(90, "90+ degree credits", opens),
               CreditTier(0, "0 degree credits", opens + timedelta(days=3))),
        all_students_opens_at=opens + timedelta(days=5),
        closure_start=datetime(2000, 1, 1, 2).time(), closure_end=datetime(2000, 1, 1, 2).time(),
        source_url="synthetic://test", source_basis="synthetic")}
    from app.models import SectionAvailability
    from sqlalchemy import select

    assert _refresh(world, at=T0).verdict == "accepted"
    opened = world.scalar(select(SectionAvailability.index_number).where(
        SectionAvailability.term_code == FALL26, SectionAvailability.status == "open")
        .order_by(SectionAvailability.index_number).limit(1))
    payload = [i for i in _payload() if i != opened]
    assert _refresh(world, payload, at=T0 + timedelta(hours=2)).verdict == "accepted"
    rows = {r.phase: r for r in changes_by_registration_phase(world, FALL26, schedules=sched)}
    assert rows["before registration"].other > 0              # first observations
    assert rows["90+ degree credits"].closed == 1             # the synthetic close
    assert changes_by_registration_phase(world, "20279") == []


def test_the_term_watcher_archives_where_the_worker_dedupes():
    """The term watcher's openSections refresh used `<cache>/availability` -
    outside the worker container's only mounted archive, so its files were
    lost on recreate and never compared by the sha256 dedupe."""
    from coursepilot_ingestion import live_cli, term_watch_cli
    from coursepilot_ingestion.cli import ROOT

    assert term_watch_cli.LIVE_ARCHIVE == ROOT / "data" / "raw" / "live"
    source = (ROOT / "ingestion" / "coursepilot_ingestion" / "live_cli.py").read_text("utf-8")
    assert 'archive_dir=ROOT / "data" / "raw" / "live"' in source
    del live_cli
    assert T0.tzinfo is UTC
