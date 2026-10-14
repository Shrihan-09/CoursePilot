"""Section monitoring and notification events on real Rutgers data (Phase 6.8).

Structure: the real SOC records of the Phase 6.6.1 component fixtures
(01:750:193/194 + "LB" labs, 203, 205, 227, 229, 202 ...). Availability:
the real openSections.json payload fetched 2026-10-14T02:29:51Z (T0), applied
as the FIRST refresh - so every section's first state is real:

    01:750:194 lectures 13370-13377 all closed; LB labs 13378-13385: only 13384 open
    01:750:229: 13551, 13564 open        01:750:227: only 13523 open

Every LATER refresh in these tests changes a COPY of that payload and is
labelled SYNTHETIC: Rutgers' two real polls 80 s apart differed in nothing,
so no real closed -> open transition exists to replay. The sections, their
records, labs and co-requisites are Rutgers' own.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta

import httpx
import pytest
from app.db.base import Base
from app.domain.monitoring import is_opening
from app.models import (
    AvailabilityRefresh,
    Course,
    CourseOffering,
    CourseSection,
    SectionAvailability,
    SectionAvailabilityChange,
    UserAccount,
)
from app.models.monitoring import (
    AvailabilityChangeDispatch,
    NotificationEvent,
    SectionWatch,
)
from app.services.monitoring import (
    TargetNotFound,
    TargetRemoved,
    create_watch,
    deactivate_removed_targets,
    deactivate_watch,
    describe_watches,
    expire_term,
    list_events,
    list_watches,
    pending_openings,
    process_openings,
)
from app.services.scheduling.service import generate_schedule
from sqlalchemy import event as sa_event
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from coursepilot_ingestion.pipelines.availability import AvailabilityRefreshPipeline
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import FIXTURE_DIR
from .test_live_availability import (  # noqa: F401 - the shared real-data world
    BASE_194,
    FALL25,
    FALL26,
    FALL_URL,
    LAB_194,
    PAYLOAD,
    T0,
    _payload,
    _refresh,
    _student,
    world,
)

BEFORE = T0 - timedelta(hours=1)               # watches exist before the observations
T1, T2, T3 = (T0 + timedelta(minutes=m) for m in (2, 4, 6))


def _account(session) -> uuid.UUID:
    account = UserAccount(identity_provider="dev", external_subject=f"mon-{uuid.uuid4().hex}")
    session.add(account)
    session.flush()
    return account.id


def _watch(session, account, index, *, term=FALL26, at=BEFORE):
    watch, _ = create_watch(session, account, term_code=term, index_number=index, now=at)
    session.commit()
    return watch


def _poll(session, *, add=(), drop=(), at=T1, base=None):
    """A refresh at `at`: the real T0 payload (or `base`) plus/minus indexes - SYNTHETIC."""
    entries = [i for i in (base if base is not None else _payload()) if i not in drop]
    return _refresh(session, entries + [i for i in add if i not in entries], at=at)


def _process(session, at=None, **kw):
    return process_openings(session, now=at or T1 + timedelta(seconds=30), **kw)


def _events(session, account=None):
    query = select(NotificationEvent)
    if account is not None:
        query = query.where(NotificationEvent.user_account_id == account)
    return session.scalars(query.order_by(NotificationEvent.observed_at,
                                          NotificationEvent.index_number)).all()


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


@pytest.fixture
def armed(world):  # noqa: F811
    """The real first observation (T0) - every section's first state, no openings."""
    first = _refresh(world)
    assert first.verdict == "accepted"
    return world


# ==========================================================================
# Transition semantics
# ==========================================================================


def test_only_closed_to_open_is_an_opening() -> None:
    assert is_opening("closed", "open")
    for before, after in [(None, "open"), ("unknown", "open"), ("open", "closed"),
                          ("open", "open"), ("closed", "closed"), ("closed", "unknown"),
                          ("open", "unknown"), (None, "closed"), ("unknown", "closed")]:
        assert not is_opening(before, after), (before, after)


def test_a_closed_to_open_one_watcher_exactly_one_event(armed) -> None:
    """Real lab 01:750:194 LB 13385 (closed at T0); SYNTHETIC opening at T1."""
    account = _account(armed)
    watch = _watch(armed, account, "13385")
    _poll(armed, add=["13385"])
    stats = _process(armed)
    assert (stats.openings, stats.matches, stats.events_created) == (1, 1, 1)
    [event] = _events(armed)
    assert (event.user_account_id, event.watch_id, event.kind) == (account, watch.id,
                                                                    "section_opened")
    assert (event.term_code, event.index_number, event.course_string, event.supplement_code,
            event.from_status, event.to_status) == (FALL26, "13385", "01:750:194", "LB",
                                                    "closed", "open")
    section = armed.scalar(select(CourseSection).where(CourseSection.index_number == "13385"))
    assert event.section_number == section.section_number and event.section_id == section.id
    assert _utc(event.observed_at) == T1                    # when observed - "opened at"
    change = armed.get(SectionAvailabilityChange, event.change_id)
    assert (change.from_status, change.to_status, change.index_number) == (
        "closed", "open", "13385")


def test_b_many_watchers_one_event_each_and_others_untouched(armed) -> None:
    accounts = [_account(armed) for _ in range(3)]
    for a in accounts:
        _watch(armed, a, "13385")
    bystander = _account(armed)
    _watch(armed, bystander, "13379")                        # another lab, stays closed
    _poll(armed, add=["13385"])
    stats = _process(armed)
    assert (stats.openings, stats.events_created) == (1, 3)
    assert sorted(e.user_account_id for e in _events(armed)) == sorted(accounts)
    assert _events(armed, bystander) == []


def test_c_reprocessing_never_duplicates(armed) -> None:
    for _ in range(2):
        _watch(armed, _account(armed), "13385")
    _poll(armed, add=["13385"])
    assert _process(armed).events_created == 2
    again = _process(armed)
    assert (again.openings, again.events_created) == (0, 0)
    # Lose the processed markers (a crash between two commits elsewhere):
    # the opening is pending again, and the unique key still inserts nothing.
    for d in armed.scalars(select(AvailabilityChangeDispatch)):
        armed.delete(d)
    armed.commit()
    assert pending_openings(armed) == 1
    redo = _process(armed)
    assert (redo.openings, redo.matches, redo.events_created) == (1, 2, 0)
    assert len(_events(armed)) == 2 and pending_openings(armed) == 0


def test_c_the_database_rejects_a_second_event_for_the_same_pair(armed) -> None:
    _watch(armed, _account(armed), "13385")
    _poll(armed, add=["13385"])
    _process(armed)
    [event] = _events(armed)
    dup = NotificationEvent(**{c.name: getattr(event, c.name)
                               for c in NotificationEvent.__table__.columns if c.name != "id"})
    armed.add(dup)
    with pytest.raises(IntegrityError):
        armed.flush()
    armed.rollback()


def test_d_open_to_open_is_not_an_event(armed) -> None:
    _watch(armed, _account(armed), "13384")                  # real: open at T0
    _poll(armed)                                             # still open
    assert _process(armed).openings == 0 and _events(armed) == []


def test_e_closed_to_closed_is_not_an_event(armed) -> None:
    _watch(armed, _account(armed), "13370")                  # real: closed at T0
    _poll(armed)
    assert _process(armed).openings == 0 and _events(armed) == []


def test_f_open_closed_open_fires_only_on_the_reopening(armed) -> None:
    account = _account(armed)
    _watch(armed, account, "13384")
    _poll(armed, drop=["13384"], at=T1)                      # SYNTHETIC: open -> closed
    assert _process(armed).events_created == 0
    _poll(armed, at=T2)                                      # SYNTHETIC: closed -> open
    assert _process(armed, at=T2 + timedelta(seconds=30)).events_created == 1
    [event] = _events(armed, account)
    assert _utc(event.observed_at) == T2


def test_g_first_observation_and_unknown_are_never_just_opened(world) -> None:  # noqa: F811
    """The FIRST refresh of a term records ~every section NULL -> state; an
    open section's first observation is not an opening. UNKNOWN -> OPEN is
    not one either: CoursePilot did not know it was closed."""
    account = _account(world)
    _watch(world, account, "13384")                          # watch BEFORE any observation
    first = _refresh(world)
    assert first.transitions > 0
    assert _process(world, at=T0 + timedelta(seconds=30)).openings == 0
    # SYNTHETIC: a snapshot left 'unknown', then observed open.
    _watch(world, account, "13385")
    row = world.scalar(select(SectionAvailability).where(
        SectionAvailability.index_number == "13385"))
    row.status = "unknown"
    world.commit()
    _poll(world, add=["13385"])
    changed = world.scalars(select(SectionAvailabilityChange).where(
        SectionAvailabilityChange.index_number == "13385")).all()
    assert ("unknown", "open") in {(c.from_status, c.to_status) for c in changed}
    assert _process(world).openings == 0 and _events(world) == []


def test_h_events_state_what_was_observed_never_now(armed) -> None:
    """Processing a day late still records the opening AT T1, with the
    component context as of the refresh it used - no 'open now' claim."""
    _watch(armed, _account(armed), "13385")
    _poll(armed, add=["13385"])
    _process(armed, at=T1 + timedelta(days=1))
    [event] = _events(armed)
    assert _utc(event.observed_at) == T1 and _utc(event.context_as_of) == T1
    assert not {"is_open", "open_now", "seats"} & {c.name for c in
                                                  NotificationEvent.__table__.columns}


def test_h_a_watch_reports_the_targets_state_with_its_freshness(armed) -> None:
    account = _account(armed)
    closed, opened = _watch(armed, account, "13385"), _watch(armed, account, "13384")
    described = describe_watches(armed, [closed, opened], T0 + timedelta(minutes=1))
    assert described[closed.id][1].status.value == "closed"
    assert described[opened.id][1].status.value == "open"     # watchable: waits for the NEXT opening
    assert described[opened.id][1].freshness.value == "current"
    later = describe_watches(armed, [opened], T0 + timedelta(hours=2))
    assert later[opened.id][1].freshness.value == "stale"     # never "current" from an old poll


def test_i_duplicate_creation_is_one_active_watch(armed) -> None:
    account = _account(armed)
    first, created = create_watch(armed, account, term_code=FALL26, index_number="13385",
                                  now=BEFORE)
    second, again = create_watch(armed, account, term_code=FALL26, index_number="13385",
                                 now=BEFORE + timedelta(minutes=5))
    armed.commit()
    assert created and not again and first.id == second.id
    # The partial unique index - not only the lookup - forbids a second active one.
    armed.add(SectionWatch(user_account_id=account, section_id=first.section_id,
                           term_code=FALL26, index_number="13385"))
    with pytest.raises(IntegrityError):
        armed.flush()
    armed.rollback()
    assert len(list_watches(armed, account)) == 1


def test_j_a_deactivated_watch_gets_nothing_and_stays_as_history(armed) -> None:
    account = _account(armed)
    watch = _watch(armed, account, "13385")
    deactivate_watch(armed, account, watch.id, now=BEFORE + timedelta(minutes=1))
    armed.commit()
    _poll(armed, add=["13385"])
    assert _process(armed).events_created == 0
    history = list_watches(armed, account, include_inactive=True)
    assert [(w.id, w.deactivation_reason) for w in history] == [(watch.id, "user_cancelled")]
    again = _watch(armed, account, "13385", at=T1 + timedelta(minutes=1))   # a new watch row
    assert again.id != watch.id


def test_k_a_reused_index_in_another_term_never_matches(world) -> None:  # noqa: F811
    """SYNTHETIC reuse, set up before any observation: the Fall 2026 lab 13385
    carries the index of a real Fall 2025 section (Rutgers really reuses
    indexes - 116 Spring 2026 open indexes are Fall 2026 indexes)."""
    armed = world
    old = armed.scalar(select(CourseSection).where(CourseSection.term_code == FALL25)
                       .order_by(CourseSection.index_number))
    reused = old.index_number
    lab = armed.scalar(select(CourseSection).where(CourseSection.index_number == "13385"))
    lab.index_number = reused
    armed.commit()
    _refresh(armed)                                          # first observation: closed
    fall25_watcher, fall26_watcher = _account(armed), _account(armed)
    _watch(armed, fall25_watcher, reused, term=FALL25)
    _watch(armed, fall26_watcher, reused, term=FALL26)
    _poll(armed, add=[reused])
    _process(armed)
    assert _events(armed, fall25_watcher) == []
    assert [e.term_code for e in _events(armed, fall26_watcher)] == [FALL26]


def test_l_a_lecture_opening_with_every_lab_closed_says_so(armed) -> None:
    """Real 01:750:194: lecture 13372 + the LB record. SYNTHETIC: 13372 opens
    while every LB lab is closed (13384 closes in the same poll)."""
    account = _account(armed)
    _watch(armed, account, "13372")
    _poll(armed, add=["13372"], drop=["13384"])
    _process(armed)
    [event] = _events(armed, account)
    assert (event.course_string, event.supplement_code) == ("01:750:194", "")
    assert (event.required_component_state, event.required_components_open,
            event.required_components_total) == ("closed", 0, 8)


def test_l_the_same_opening_with_a_lab_open_says_that_too(armed) -> None:
    account = _account(armed)
    _watch(armed, account, "13372")
    _poll(armed, add=["13372"])                              # real lab 13384 still open
    _process(armed)
    [event] = _events(armed, account)
    assert (event.required_component_state, event.required_components_open,
            event.required_components_total) == ("open", 1, 8)


def test_l_a_lab_opening_reports_the_lecture_record(armed) -> None:
    _watch(armed, _account(armed), "13385")
    _poll(armed, add=["13385"])
    _process(armed)
    [event] = _events(armed)
    assert (event.required_component_state, event.required_components_open,
            event.required_components_total) == ("closed", 0, 8)    # every lecture closed


def test_m_an_academic_corequisite_is_not_a_registration_component(armed) -> None:
    """Real 01:750:229 requires 01:750:227 (Phase 6.4). A 229 opening carries
    NO component context: 227 is a separate course, not part of 229's bundle."""
    account = _account(armed)
    closed_229 = armed.scalar(
        select(CourseSection.index_number)
        .join(CourseOffering, CourseOffering.id == CourseSection.offering_id)
        .join(Course, Course.id == CourseOffering.course_id)
        .where(Course.course_string == "01:750:229", CourseSection.term_code == FALL26,
               CourseSection.index_number.notin_(["13551", "13564"]))
        .order_by(CourseSection.index_number).limit(1))
    _watch(armed, account, closed_229)
    _poll(armed, add=[closed_229])
    _process(armed)
    [event] = _events(armed, account)
    assert event.course_string == "01:750:229"
    assert event.required_component_state is None and event.required_components_total is None
    result = generate_schedule(armed, _student(armed), term_code=FALL26,
                               courses=["01:750:227", "01:750:229"],
                               reference_time=T1 + timedelta(minutes=1))
    kinds = {(r.kind, r.course, r.related) for r in result.relationships}
    assert ("academic_corequisite", "01:750:229", "01:750:227") in kinds


def _reload_without(session, tmp_path, drop_index: str):
    """A newer COMPLETE Fall 2026 download (SYNTHETIC: one section removed),
    through the production course and section pipelines."""
    records = json.loads((FIXTURE_DIR / "soc_components_fall2026_sample.json").read_bytes())
    for course in records:
        course["sections"] = [s for s in course["sections"] if s["index"] != drop_index]
    folder = tmp_path / "newer"
    folder.mkdir()
    (folder / "soc_courses_2026_9_NB.json").write_text(json.dumps(records), encoding="utf-8")
    query = SocQuery(year=2026, term="9", campus="NB")
    CourseIngestionPipeline(session, folder).run(query, coverage="complete")
    SectionIngestionPipeline(session, folder).run(query)
    session.commit()


def test_n_a_removed_section_cannot_be_watched_nor_fire(armed, tmp_path) -> None:
    account = _account(armed)
    watch = _watch(armed, account, "13385")
    _reload_without(armed, tmp_path, "13385")
    with pytest.raises(TargetRemoved):
        create_watch(armed, _account(armed), term_code=FALL26, index_number="13385", now=T1)
    armed.rollback()
    view = describe_watches(armed, [watch], T1)[watch.id][0]
    assert view.removed and view.course_string == "01:750:194"   # explicit, not retargeted
    assert deactivate_removed_targets(armed, now=T1) == 1
    armed.commit()
    assert armed.get(SectionWatch, watch.id).deactivation_reason == "target_removed"
    _poll(armed, add=["13385"], at=T2)    # even if listed again, an inactive watch gets nothing
    _process(armed, at=T2)
    assert _events(armed, account) == []


def test_n_a_deleted_section_keeps_the_watch_as_history(armed) -> None:
    account = _account(armed)
    watch = _watch(armed, account, "13385")
    armed.delete(armed.scalar(select(CourseSection).where(CourseSection.index_number == "13385")))
    armed.commit()
    armed.expire_all()                                       # the database did the SET NULL
    kept = armed.get(SectionWatch, watch.id)
    assert kept.section_id is None and (kept.term_code, kept.index_number) == (FALL26, "13385")
    assert deactivate_removed_targets(armed, now=T1) == 1
    _poll(armed, add=["13385"])
    assert _process(armed).events_created == 0


def test_unknown_targets_are_refused(armed) -> None:
    with pytest.raises(TargetNotFound):
        create_watch(armed, _account(armed), term_code=FALL26, index_number="99999", now=T1)
    with pytest.raises(TargetNotFound):           # a real index, but of another term
        create_watch(armed, _account(armed), term_code="20271", index_number="13385", now=T1)


def test_a_watch_created_after_an_opening_does_not_get_it(armed) -> None:
    _poll(armed, add=["13385"])                              # opening observed at T1
    late = _account(armed)
    _watch(armed, late, "13385", at=T1 + timedelta(minutes=1))
    _process(armed, at=T1 + timedelta(minutes=2))
    assert _events(armed, late) == []


def test_o_one_account_never_sees_or_changes_anothers(armed) -> None:
    a, b = _account(armed), _account(armed)
    watch_a = _watch(armed, a, "13385")
    _poll(armed, add=["13385"])
    _process(armed)
    assert deactivate_watch(armed, b, watch_a.id, now=T2) is None
    armed.commit()
    assert armed.get(SectionWatch, watch_a.id).deactivated_at is None
    assert list_watches(armed, b, include_inactive=True) == []
    assert list_events(armed, b, limit=50) == []
    assert len(list_events(armed, a, limit=50)) == 1


def test_q_the_server_turns_a_refresh_into_events_with_no_client(armed, tmp_path) -> None:
    """The refresh pipeline processes openings itself: nobody has to be
    online, call the API or keep a page open. ONE Rutgers request per term."""
    account = _account(armed)
    _watch(armed, account, "13385")
    body = json.dumps(_payload() + ["13385"]).encode()
    seen = []

    def rutgers(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=body)

    refresh = AvailabilityRefreshPipeline(armed, tmp_path, transport=httpx.MockTransport(
        rutgers)).run(SocQuery(year=2026, term="9", campus="NB"))
    assert seen == [FALL_URL] and refresh.verdict == "accepted"
    [event] = _events(armed, account)
    assert event.index_number == "13385"


def test_expired_terms_keep_history_and_stop_matching(armed) -> None:
    account = _account(armed)
    watch = _watch(armed, account, "13385")
    assert expire_term(armed, FALL26, now=T1) == 1
    armed.commit()
    _poll(armed, add=["13385"], at=T2)
    assert _process(armed, at=T2).events_created == 0
    assert armed.get(SectionWatch, watch.id).deactivation_reason == "term_ended"


# ==========================================================================
# Determinism, read-only, query budget, Rutgers request count
# ==========================================================================


def test_processing_is_deterministic_whatever_the_batch_size(armed) -> None:
    accounts = [_account(armed) for _ in range(3)]
    for i, a in enumerate(accounts):
        for index in ("13385", "13372", "13379")[: i + 1]:
            _watch(armed, a, index)
    _poll(armed, add=["13385", "13372", "13379"])
    stats = _process(armed, batch=1)
    assert stats.batches == 3 and stats.events_created == 6
    got = sorted((str(e.user_account_id), e.index_number) for e in _events(armed))
    expected = sorted((str(a), index) for i, a in enumerate(accounts)
                      for index in ("13385", "13372", "13379")[: i + 1])
    assert got == expected
    listed = [e.index_number for e in list_events(armed, accounts[2], limit=10)]
    assert listed == sorted(listed)                          # explicit, stable order


def test_each_event_is_linked_to_its_own_transition(armed) -> None:
    """Several openings in ONE batch: every event points at the change row of
    its own section, with that change's time and refresh."""
    account = _account(armed)
    for index in ("13385", "13372", "13379"):
        _watch(armed, account, index)
    _poll(armed, add=["13385", "13372"], at=T1)
    _poll(armed, add=["13385", "13372", "13379"], at=T2)
    stats = _process(armed, at=T2 + timedelta(seconds=30))
    assert (stats.batches, stats.openings, stats.events_created) == (1, 3, 3)
    for e in _events(armed, account):
        change = armed.get(SectionAvailabilityChange, e.change_id)
        assert (change.index_number, change.section_id, change.refresh_id,
                _utc(change.observed_at)) == (e.index_number, e.section_id, e.refresh_id,
                                              _utc(e.observed_at))
    assert [(e.index_number, _utc(e.observed_at)) for e in _events(armed, account)] == [
        ("13372", T1), ("13385", T1), ("13379", T2)]


def _hashes(session) -> dict[str, str]:
    out = {}
    for table in Base.metadata.sorted_tables:
        rows = sorted(repr(tuple(r)) for r in session.execute(select(table)).all())
        out[table.name] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
    return out


def test_schedule_generation_creates_no_watch_and_processes_nothing(armed) -> None:
    _watch(armed, _account(armed), "13385")
    _poll(armed, add=["13385"])                              # an opening is pending
    before = _hashes(armed)
    generate_schedule(armed, _student(armed), term_code=FALL26, courses=["01:750:194"],
                      reference_time=T1 + timedelta(minutes=1))
    armed.rollback()
    after = _hashes(armed)
    changed = {t for t in before if before[t] != after[t]}
    # _student() wrote the student; generation itself wrote nothing.
    assert changed <= {"student", "student_course"}
    assert {"section_watch", "notification_event", "availability_change_dispatch"} <= set(after)
    assert pending_openings(armed) == 1


@pytest.mark.parametrize("watchers", [0, 1, 10, 100])
def test_processing_cost_does_not_grow_with_watchers(armed, watchers) -> None:
    for _ in range(watchers):
        _watch(armed, _account(armed), "13385")
    _poll(armed, add=["13385"])
    statements = []
    engine = armed.get_bind()

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    sa_event.listen(engine, "before_cursor_execute", count)
    try:
        stats = _process(armed)
    finally:
        sa_event.remove(engine, "before_cursor_execute", count)
    assert stats.events_created == watchers
    inserts = [s for s in statements if s.lstrip().upper().startswith("INSERT INTO NOTIFICATION")]
    assert len(inserts) == (1 if watchers else 0)           # one set-based insert, any count
    assert len(statements) <= 20


def test_watches_never_add_a_rutgers_request(armed, tmp_path) -> None:
    """The invariant: ONE request per term per refresh, with 0 or 200 watches."""
    requests = []

    def rutgers(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=PAYLOAD.read_bytes())

    pipeline = AvailabilityRefreshPipeline(armed, tmp_path, transport=httpx.MockTransport(rutgers))
    query = SocQuery(year=2026, term="9", campus="NB")
    pipeline.run(query)
    for _ in range(200):
        _watch(armed, _account(armed), "13385")
    pipeline.run(query)
    assert requests == [FALL_URL, FALL_URL]
    assert _count(armed, AvailabilityRefresh) == 3           # the fixture's + these two


def _utc(value):
    from datetime import UTC
    return value if value.tzinfo else value.replace(tzinfo=UTC)
