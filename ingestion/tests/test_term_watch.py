"""Phase 7.3 term watcher: classification, idempotency, cadence, evidence-only promotion.

Public SOC sample records stand in for a published term; no network.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta

from app.models import DataSource, RutgersTerm
from app.services.term_authority import TermAuthority, term_states
from sqlalchemy import func, select

from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.term_watch import (
    STABLE_FETCHES,
    WatchState,
    due,
    promotion_evidence,
    term_start,
    watch,
)

from .conftest import FIXTURE_DIR

TERM = "20279"                     # Fall 2027: classes start 2027-09-01
T0 = datetime(2027, 3, 1, 12, tzinfo=UTC)


def _sample():
    return json.loads((FIXTURE_DIR / "soc_cs_courses_sample.json").read_bytes())


class Source:
    """A fake SOC: what each term returns; counts every request."""

    def __init__(self, tmp_path, payloads):
        self.tmp, self.payloads, self.calls = tmp_path, payloads, []

    def __call__(self, query):
        self.calls.append(query.term_code)
        value = self.payloads.get(query.term_code, [])
        if isinstance(value, Exception):
            raise value
        content = json.dumps(value).encode()
        (self.tmp / f"soc_courses_{query.year}_{query.term}_NB.json").write_bytes(content)
        return content, hashlib.sha256(content).hexdigest()


def _run(session, tmp_path, source, at, terms=(TERM,), baseline=None):
    return watch(
        session, source, terms=list(terms), now=lambda: at, baseline=baseline,
        load_courses=lambda q: CourseIngestionPipeline(session, tmp_path).run(
            q, coverage="partial"),
        load_sections=lambda q: SectionIngestionPipeline(session, tmp_path).run(q))


def _sources(session):
    return session.scalar(select(func.count()).select_from(DataSource)
                          .where(DataSource.term_code == TERM))


def test_classification_published_empty_and_failed(session, tmp_path):
    source = Source(tmp_path, {TERM: _sample(), "20281": [], "20277": OSError("down")})
    out = {o.term_code: o for o in _run(session, tmp_path, source, T0,
                                        terms=(TERM, "20281", "20277"))}
    assert out[TERM].state == WatchState.PUBLISHED and out[TERM].action == "loaded"
    assert out["20281"].state == WatchState.EMPTY_UNPUBLISHED     # 200 + [] is not a failure
    assert out["20277"].state == WatchState.FETCH_FAILED          # and a failure is not "empty"
    assert source.calls == [TERM, "20281", "20277"]               # one request per term
    assert term_states(session)[TERM].authority is TermAuthority.READY


def test_loaded_term_stays_partial_without_evidence(session, tmp_path):
    _run(session, tmp_path, Source(tmp_path, {TERM: _sample()}), T0)
    row = session.scalar(select(RutgersTerm))
    assert row.coverage_state == "partial"
    assert session.scalar(select(DataSource.coverage).where(DataSource.term_code == TERM)) \
        == "partial"


def test_unchanged_payload_writes_no_academic_data(session, tmp_path):
    source = Source(tmp_path, {TERM: _sample()})
    _run(session, tmp_path, source, T0)
    before = _sources(session)
    out = _run(session, tmp_path, source, T0 + timedelta(days=1))
    assert out[0].action.startswith("unchanged")
    assert _sources(session) == before                             # no new provenance rows


def test_changed_payload_is_a_new_versioned_source(session, tmp_path):
    records = _sample()
    source = Source(tmp_path, {TERM: records})
    _run(session, tmp_path, source, T0)
    changed = copy.deepcopy(records)
    changed[0]["title"] = changed[0]["title"] + " (SYNTHETIC EDIT)"
    source.payloads[TERM] = changed
    out = _run(session, tmp_path, source, T0 + timedelta(days=1))
    assert out[0].action == "reloaded"
    assert _sources(session) == 2                                  # versioned, never overwritten


def test_not_due_means_no_request(session, tmp_path):
    source = Source(tmp_path, {TERM: _sample()})
    _run(session, tmp_path, source, T0)
    out = _run(session, tmp_path, source, T0 + timedelta(hours=3))
    assert out[0].action == "skipped_not_due" and source.calls == [TERM]


def test_cadence_daily_before_classes_weekly_after(session, tmp_path):
    _run(session, tmp_path, Source(tmp_path, {TERM: _sample()}), T0)
    row = session.scalar(select(RutgersTerm))
    assert term_start(TERM).isoformat() == "2027-09-01"
    assert due(row, T0 + timedelta(days=1))                        # before classes: daily
    after = datetime(2027, 9, 10, tzinfo=UTC)
    row.last_checked_at = after - timedelta(days=3)
    assert not due(row, after)                                     # after classes: weekly
    assert due(row, after + timedelta(days=4))


def test_promotion_needs_stable_counts_over_time(session, tmp_path):
    source = Source(tmp_path, {TERM: _sample()})
    for day in range(STABLE_FETCHES):                              # 3 fetches over 2 days
        out = _run(session, tmp_path, source, T0 + timedelta(days=day))
    row = session.scalar(select(RutgersTerm))
    assert out[-1].action.endswith("+promoted") and row.coverage_state == "complete"
    assert "unchanged across 3 fetches" in row.coverage_evidence
    assert session.scalar(select(DataSource.coverage).where(DataSource.term_code == TERM)) \
        == "complete"


def _obs(hours, sections=100):
    return {"at": (T0 + timedelta(hours=hours)).isoformat(), "hash": "h",
            "courses": 10, "sections": sections}


def test_no_promotion_when_counts_move_span_too_short_or_too_few(session, tmp_path):
    _run(session, tmp_path, Source(tmp_path, {TERM: _sample()}), T0)
    row = session.scalar(select(RutgersTerm))
    row.observations = [_obs(0), _obs(25), _obs(49)]
    assert promotion_evidence(row) is not None                     # control: all rules met
    row.observations = [_obs(0), _obs(25), _obs(49, sections=101)]
    assert promotion_evidence(row) is None                         # counts moved
    row.observations = [_obs(0), _obs(1), _obs(2)]
    assert promotion_evidence(row) is None                         # same counts, 2 hours only
    row.observations = [_obs(0), _obs(49)]
    assert promotion_evidence(row) is None                         # span met, too few fetches


def test_structure_change_blocks_the_load(session, tmp_path):
    records = _sample()
    for course in records:
        for section in course["sections"]:
            section.pop("meetingTimes", None)                      # SYNTHETIC: field removed
    out = _run(session, tmp_path, Source(tmp_path, {TERM: records}), T0,
               baseline=lambda: _sample())
    assert out[0].action == "blocked" and "missing_section_fields" in out[0].detail["blocking"]
    assert session.scalar(select(RutgersTerm)).ingestion_state != "ingested"
    assert _sources(session) == 0


def test_the_live_worker_runs_the_watcher_as_a_rate_limited_step(session, tmp_path):
    """One loop, not two: the watcher is a low-frequency LiveWorker step."""
    from sqlalchemy.orm import sessionmaker

    from coursepilot_ingestion.live_worker import LiveWorker
    from coursepilot_ingestion.term_watch import TermOutcome

    calls, clock = [], {"now": T0}

    def stub(s):
        calls.append(clock["now"])
        return [TermOutcome(TERM, "published", "unchanged")]

    worker = LiveWorker(sessionmaker(session.get_bind(), expire_on_commit=False), owner="t",
                        archive_dir=tmp_path, clock=lambda: clock["now"], term_watch=stub,
                        term_watch_every=timedelta(hours=6))
    assert worker.tick().term_watch == {TERM: "unchanged"}
    clock["now"] = T0 + timedelta(hours=1)
    assert worker.tick().term_watch == {} and len(calls) == 1      # not yet due
    clock["now"] = T0 + timedelta(hours=7)
    worker.tick()
    assert len(calls) == 2
