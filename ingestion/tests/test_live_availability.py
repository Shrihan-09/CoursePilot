"""Registration availability on real Rutgers data (Phase 6.7).

Structure comes from the real SOC records of the Phase 6.6.1 component
fixtures (01:750:193/194 + "LB" labs, 203, 205, 227, 229, 01:119:117 ...),
loaded by the production course and section pipelines. Availability comes
from the real openSections.json payload fetched 2026-10-14T02:29:51Z
(filtered to these sections plus 25 other-campus indexes) - see
scripts/make_availability_fixture.py. The structural download is the
archived 2026-09-08 one, so the two sources genuinely differ (real drift:
01:750:194 section 13372 and LB 13378 were open on 09-08, closed on 10-14).

Real states at T0 (open / sections):
    01:750:193 0/9, LB 0/9    01:750:194 0/8, LB 1/8 (13384)    01:750:203 2/41
    01:750:205 2/33           01:750:227 1/33 (13523)           01:750:229 2/27
No real course had BOTH a lecture and its LB lab open, or a lab whose state
was unknown: those cases change a COPY of the payload and say SYNTHETIC.

No test touches the network: the fetcher runs on an injected transport.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.db.base import Base
from app.domain.availability import RefreshVerdict
from app.domain.schedule import (
    AvailabilityFreshness,
    AvailabilityMode,
    AvailabilityState,
    Reason,
    SchedulePreferences,
    ScheduleStatus,
)
from app.models import (
    AvailabilityRefresh,
    Course,
    CourseSection,
    DataSource,
    ProgramVersion,
    SectionAvailability,
    SectionAvailabilityChange,
    Student,
    StudentCourse,
)
from app.services.availability import apply_refresh, lookup
from app.services.scheduling.service import generate_schedule
from sqlalchemy import event, func, select

from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.pipelines.availability import (
    AvailabilityRefreshPipeline,
    from_archive,
)
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import CS_REQUIREMENTS, FIXTURE_DIR

FALL26, FALL25 = "20269", "20259"
T0 = datetime(2026, 10, 14, 2, 29, 51, tzinfo=UTC)            # the real fetch
NOW = T0 + timedelta(minutes=1)                                # "the request"
DOWNLOAD = datetime(2026, 9, 8, tzinfo=UTC)                    # the archived courses.json
FALL_URL = "https://classes.rutgers.edu/soc/api/openSections.json?year=2026&term=9&campus=NB"
PAYLOAD = FIXTURE_DIR / "open_sections_fall2026_20261014T022951Z.json"
COMPONENTS = {(2026, "9"): "soc_components_fall2026_sample.json",
              (2025, "9"): "soc_components_fall2025_sample.json",
              (2026, "7"): "soc_components_summer2026_sample.json"}
PHYSICS = [("01:750:193", "20259"), ("01:750:271", "20259"), ("01:640:151", "20259"),
           ("01:640:152", "20259"), ("01:119:115", "20259"), ("01:160:307", "20259"),
           ("01:160:313", "20261")]
LAB_194 = ["13378", "13379", "13380", "13381", "13382", "13383", "13384", "13385"]
BASE_194 = ["13370", "13371", "13372", "13373", "13374", "13375", "13376", "13377"]


def _load(session, tmp_path, year, term):
    query = SocQuery(year=year, term=term, campus="NB")
    folder = tmp_path / f"{year}{term}"
    folder.mkdir(exist_ok=True)
    (folder / f"soc_courses_{year}_{term}_NB.json").write_bytes(
        (FIXTURE_DIR / COMPONENTS[(year, term)]).read_bytes())
    CourseIngestionPipeline(session, folder).run(query, coverage="complete")
    stats = SectionIngestionPipeline(session, folder).run(query)
    assert stats.unmatched_offering == 0 and stats.validation_failed == 0


@pytest.fixture
def world(session, tmp_path):
    for year, term in COMPONENTS:
        _load(session, tmp_path, year, term)
    RequirementLoader(session).load_file(CS_REQUIREMENTS)
    # The fixture copy's file time is "now"; the payload was downloaded 2026-09-08.
    for source in session.scalars(select(DataSource).where(DataSource.term_code == FALL26)):
        source.retrieved_at = DOWNLOAD
    session.commit()
    return session


def _student(session):
    version = session.scalar(select(ProgramVersion))
    student = Student(external_ref=f"av-{uuid.uuid4().hex[:8]}",
                      catalog_year=version.catalog_year, program_version_id=version.id)
    session.add(student)
    session.flush()
    for key, term in PHYSICS:
        course = session.scalar(select(Course).where(Course.course_string == key,
                                                     Course.supplement_code == ""))
        session.add(StudentCourse(student_id=student.id, course_id=course.id, term_code=term,
                                  status="completed", grade="A", credits_earned=course.credits))
    session.commit()
    return student


def _payload() -> list[str]:
    return json.loads(PAYLOAD.read_bytes())


def _refresh(session, entries=None, *, at=T0, url=FALL_URL, term=FALL26, body=None):
    body = body if body is not None else json.dumps(
        _payload() if entries is None else entries).encode()
    refresh = apply_refresh(session, term_code=term, body=body, observed_at=at, source_url=url)
    session.commit()
    return refresh


def _gen(session, student, courses, *, mode=AvailabilityMode.PREFER_OPEN, at=NOW,
         term=FALL26, **kw):
    return generate_schedule(session, student, term_code=term, courses=courses,
                             preferences=SchedulePreferences(availability=mode),
                             reference_time=at, **kw)


def _snapshot(session) -> dict[str, str]:
    return {s.index_number: s.status for s in session.scalars(
        select(SectionAvailability).where(SectionAvailability.term_code == FALL26))}


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _fall_sections(session) -> int:
    return session.scalar(select(func.count()).select_from(CourseSection)
                          .where(CourseSection.term_code == FALL26))


def _course(option, course):
    return next(c for c in option.availability.courses if c.course == course)


# ==========================================================================
# The refresh: its own boundary, guarded, idempotent
# ==========================================================================


def test_a_real_payload_sets_every_section_of_the_term(world) -> None:
    sections = _fall_sections(world)
    refresh = _refresh(world)
    assert refresh.verdict == RefreshVerdict.ACCEPTED.value
    snap = _snapshot(world)
    assert len(snap) == sections                                   # every section, nothing else
    by_course = {}
    for s in world.scalars(select(CourseSection).where(CourseSection.term_code == FALL26)):
        by_course.setdefault(s.offering.course.course_string
                             + s.offering.course.supplement_code.strip(), []).append(
            snap[s.index_number])
    opened = {k: v.count("open") for k, v in by_course.items()}
    assert opened["01:750:193"] == 0 and opened["01:750:193LB"] == 0
    assert opened["01:750:194"] == 0 and opened["01:750:194LB"] == 1
    assert (opened["01:750:227"], opened["01:750:229"], opened["01:750:203"]) == (1, 2, 2)
    assert snap["13384"] == "open"
    # 25 other-campus indexes + open sections of courses not in this world: ignored
    assert refresh.matched == sum(v == "open" for v in snap.values())
    assert refresh.ignored == len(set(_payload())) - refresh.matched
    assert refresh.transitions == sections == _count(world, SectionAvailabilityChange)
    assert refresh.content_sha256 == hashlib.sha256(PAYLOAD.read_bytes()).hexdigest()


def test_reapplying_the_same_payload_changes_nothing_but_the_as_of_time(world) -> None:
    _refresh(world)
    before, changes = _snapshot(world), _count(world, SectionAvailabilityChange)
    since = {s.index_number: s.since for s in world.scalars(select(SectionAvailability))}
    again = _refresh(world, at=T0 + timedelta(minutes=2))
    assert again.verdict == "accepted" and again.transitions == 0
    assert _snapshot(world) == before and _count(world, SectionAvailabilityChange) == changes
    assert {s.index_number: s.since for s in world.scalars(select(SectionAvailability))} == since
    assert _count(world, AvailabilityRefresh) == 2


def test_change_detection_records_only_transitions(world) -> None:
    """SYNTHETIC second poll: 13385 (LB) opens, 13384 (LB) closes."""
    _refresh(world)
    changes = _count(world, SectionAvailabilityChange)
    later = T0 + timedelta(minutes=2)
    second = [i for i in _payload() if i != "13384"] + ["13385"]
    refresh = _refresh(world, second, at=later)
    assert refresh.transitions == 2
    new = world.scalars(select(SectionAvailabilityChange)
                        .where(SectionAvailabilityChange.refresh_id == refresh.id)
                        .order_by(SectionAvailabilityChange.index_number)).all()
    assert [(c.index_number, c.from_status, c.to_status) for c in new] == [
        ("13384", "open", "closed"), ("13385", "closed", "open")]
    assert _count(world, SectionAvailabilityChange) == changes + 2
    row = world.scalar(select(SectionAvailability).where(SectionAvailability.index_number
                                                         == "13385"))
    assert (row.status, _utc(row.since), row.changed_by_refresh_id) == ("open", later, refresh.id)


def test_unknown_to_open_is_a_transition(world) -> None:
    """SYNTHETIC: a snapshot row left 'unknown' (e.g. by a future source)."""
    _refresh(world)
    row = world.scalar(select(SectionAvailability).where(SectionAvailability.index_number
                                                         == "13384"))
    row.status = "unknown"
    world.commit()
    refresh = _refresh(world, at=T0 + timedelta(minutes=2))
    [change] = world.scalars(select(SectionAvailabilityChange)
                             .where(SectionAvailabilityChange.refresh_id == refresh.id)).all()
    assert (change.index_number, change.from_status, change.to_status) == (
        "13384", "unknown", "open")


@pytest.mark.parametrize(("body", "url", "verdict"), [
    (b"[]", FALL_URL, "rejected_empty"),                                   # real: Spring 2027
    (b"<html>503</html>", FALL_URL, "rejected_malformed"),                 # SYNTHETIC
    ((FIXTURE_DIR / "open_sections_other_term_2026_7.json").read_bytes(),  # real Summer 2026,
     FALL_URL, "rejected_no_overlap_with_term"),                           # mislabelled as Fall
    ((FIXTURE_DIR / "open_sections_other_term_2027_0.json").read_bytes(),  # real Winter 2027
     FALL_URL.replace("year=2026&term=9", "year=2027&term=0"), "rejected_term_mismatch"),
    ((FIXTURE_DIR / "open_sections_other_term_2026_1.json").read_bytes(),  # real Spring 2026:
     FALL_URL.replace("term=9", "term=1"), "rejected_term_mismatch"),     # shares 116 indexes
])
def test_untrustworthy_payloads_never_change_a_state(world, body, url, verdict) -> None:
    _refresh(world)
    before, changes = _snapshot(world), _count(world, SectionAvailabilityChange)
    refresh = _refresh(world, at=T0 + timedelta(minutes=2), url=url, body=body)
    assert refresh.verdict == verdict and refresh.detail
    assert _snapshot(world) == before and _count(world, SectionAvailabilityChange) == changes
    # the latest ACCEPTED refresh stays the "as of" time
    result = _gen(world, _student(world), ["01:750:203"], at=T0 + timedelta(minutes=3))
    assert result.metadata.availability_observed_at == T0.isoformat()


def test_a_collapse_after_a_full_payload_is_rejected(world) -> None:
    """SYNTHETIC: the 2026-10-14 payload, then one with 5 of its indexes."""
    first = _refresh(world)
    assert first.matched >= 100
    still_open = [i for i, s in sorted(_snapshot(world).items()) if s == "open"][:5]
    refresh = _refresh(world, still_open, at=T0 + timedelta(minutes=2))
    assert refresh.verdict == "rejected_implausible_drop"


def test_the_pipeline_fetches_archives_and_records_without_the_network(world, tmp_path) -> None:
    body = PAYLOAD.read_bytes()
    seen = []

    def ok(request):
        seen.append(str(request.url))
        assert request.headers["user-agent"].startswith("CoursePilot/")
        return httpx.Response(200, content=body)

    query = SocQuery(year=2026, term="9", campus="NB")
    pipeline = AvailabilityRefreshPipeline(world, tmp_path / "live",
                                           transport=httpx.MockTransport(ok))
    refresh = pipeline.run(query)
    assert seen == [FALL_URL]                                       # ONE request per term
    assert refresh.verdict == "accepted" and refresh.body_bytes == len(body)
    archived = list((tmp_path / "live").glob("openSections_2026_9_NB_*.json"))
    assert len(archived) == 1 and archived[0].read_bytes() == body

    failing = AvailabilityRefreshPipeline(world, tmp_path / "live", transport=httpx.MockTransport(
        lambda request: httpx.Response(503, content=b"unavailable")))
    before = _snapshot(world)
    bad = failing.run(query)
    assert (bad.verdict, bad.detail, bad.content_sha256) == ("rejected_fetch_failed",
                                                             "HTTP 503", None)
    assert _snapshot(world) == before

    replay = AvailabilityRefreshPipeline(world, tmp_path / "replay").run(
        query, fetched=from_archive(PAYLOAD, FALL_URL, T0))
    assert replay.verdict == "accepted" and replay.transitions == 0


def test_the_refresh_never_touches_structural_rows(world) -> None:
    structural = [t for t in Base.metadata.sorted_tables if t.name not in AVAILABILITY_TABLES]
    before = _hashes(world, structural)
    _refresh(world)
    _refresh(world, [i for i in _payload() if i != "13384"], at=T0 + timedelta(minutes=2))
    assert _hashes(world, structural) == before


def test_history_outlives_a_removed_section(world) -> None:
    """A section Rutgers removes from SOC: its current state goes with it,
    its transitions stay (section_id SET NULL)."""
    _refresh(world)
    section = world.scalar(select(CourseSection).where(CourseSection.index_number == "13384",
                                                       CourseSection.term_code == FALL26))
    world.delete(section)
    world.commit()
    assert "13384" not in _snapshot(world)
    kept = world.scalars(select(SectionAvailabilityChange)
                         .where(SectionAvailabilityChange.index_number == "13384")).all()
    assert kept and all(c.section_id is None for c in kept)


# ==========================================================================
# Lookup: what the schedule reads
# ==========================================================================


def test_without_a_refresh_the_download_is_labelled_stale(world) -> None:
    result = _gen(world, _student(world), ["01:750:194"])
    assert result.metadata.availability_freshness is AvailabilityFreshness.STALE
    assert result.metadata.availability_source == "courses.json"
    issue = next(i for i in result.issues if i.code == "AVAILABILITY_NOT_CURRENT")
    assert issue.severity.value == "warning" and DOWNLOAD.isoformat() in issue.message
    for o in result.options:
        for c in o.choices:
            assert c.availability.freshness is AvailabilityFreshness.STALE
            assert c.availability.observed_at == DOWNLOAD.isoformat()
            assert "structural_snapshot_not_an_availability_refresh" in c.availability.reasons
        assert o.score.closed_sections_if_current == 0          # never ranked
        assert not o.availability.all_open_now


def test_a_refresh_makes_availability_current_until_the_window_passes(world) -> None:
    _refresh(world)
    student = _student(world)
    current = _gen(world, student, ["01:750:194"])
    meta = current.metadata
    assert (meta.availability_freshness, meta.availability_source,
            meta.availability_observed_at) == (AvailabilityFreshness.CURRENT,
                                               "openSections.json", T0.isoformat())
    assert meta.availability_refresh_id and meta.availability_current_window_seconds == 600
    assert not any(i.code == "AVAILABILITY_NOT_CURRENT" for i in current.issues)
    stale = _gen(world, student, ["01:750:194"], at=T0 + timedelta(minutes=11))
    assert stale.metadata.availability_freshness is AvailabilityFreshness.STALE
    assert any(i.code == "AVAILABILITY_NOT_CURRENT" for i in stale.issues)
    for o in stale.options:
        assert o.score.closed_sections_if_current == 0
        assert o.availability.freshness is AvailabilityFreshness.STALE
        for c in o.choices:          # still the refresh's state, with its time - never "now"
            assert c.availability.source == "openSections.json"
            assert c.availability.observed_at == T0.isoformat()


def test_real_drift_the_newer_source_wins_with_its_time(world) -> None:
    """01:750:194 LB 13378: open on 2026-09-08 (courses.json), closed on
    2026-10-14 (openSections.json)."""
    _refresh(world)
    section = world.scalar(select(CourseSection).where(CourseSection.index_number == "13378"))
    assert section.open_status is True                          # the structural download
    from app.services.availability import SectionRef
    resolved, _ = lookup(world, FALL26, [SectionRef(section.id, "13378", True,
                                                    section.source_id)], NOW)
    r = resolved["13378"]
    assert (r.status.value, r.freshness.value, r.source) == ("closed", "current",
                                                             "openSections.json")


def test_sources_disagreeing_at_the_same_moment_are_unknown_and_reported(world) -> None:
    """SYNTHETIC timing: the courses.json download moved to 5 minutes before
    the refresh, so the real 09-08/10-14 differences become simultaneous."""
    for source in world.scalars(select(DataSource).where(DataSource.term_code == FALL26)):
        source.retrieved_at = T0 - timedelta(minutes=5)
    world.commit()
    _refresh(world)
    result = _gen(world, _student(world), ["01:750:194"], max_results=25)
    lab = [c for o in result.options for c in o.choices if c.index_number == "13378"]
    assert lab and lab[0].availability.state is AvailabilityState.UNKNOWN
    assert lab[0].availability.reasons == [
        "sources_disagree:courses.json=open,openSections.json=closed"]
    option = next(o for o in result.options
                  if any(c.index_number == "13378" for c in o.choices))
    assert any(i.code == "AVAILABILITY_UNKNOWN" and i.indexes == ["13378"] for i in option.issues)


# ==========================================================================
# Registration bundles (A-F)
# ==========================================================================


def test_a_ordinary_course_open(world) -> None:
    """Real: 01:750:203 (LEC+RECIT under ONE index) - 2 of 41 sections open."""
    _refresh(world)
    result = _gen(world, _student(world), ["01:750:203"], mode=AvailabilityMode.REQUIRE_OPEN)
    assert result.status is ScheduleStatus.OPTIONS_FOUND
    assert {o.choices[0].index_number for o in result.options} <= {"13390", "13415"}
    for o in result.options:
        [course] = o.availability.courses
        assert (course.state, course.freshness) == (AvailabilityState.OPEN,
                                                    AvailabilityFreshness.CURRENT)
        assert [c.component for c in course.components] == ["primary"]
        assert o.availability.all_open_now and o.availability.as_of == T0.isoformat()
        assert Reason.ALL_SECTIONS_OPEN_CURRENT in o.reasons


def test_b_lecture_and_lab_both_open(world) -> None:
    """SYNTHETIC: the real payload plus every 01:750:194 lecture index (Rutgers
    had none open at T0); the real open lab 13384 kept."""
    _refresh(world, _payload() + BASE_194)
    result = _gen(world, _student(world), ["01:750:194"], mode=AvailabilityMode.REQUIRE_OPEN,
                  max_results=25)
    assert result.status is ScheduleStatus.OPTIONS_FOUND
    for o in result.options:
        course = _course(o, "01:750:194")
        assert [c.component for c in course.components] == ["primary", "required_companion"]
        assert course.components[1].index_number == "13384"     # the only open lab
        assert course.state is AvailabilityState.OPEN and o.availability.all_open_now


def test_c_closed_lab_closes_the_bundle_and_is_never_dropped(world) -> None:
    """SYNTHETIC: lectures open, every 01:750:194 LB lab closed (13384 removed)."""
    _refresh(world, [i for i in _payload() if i != "13384"] + BASE_194)
    student = _student(world)
    result = _gen(world, student, ["01:750:194"], max_results=25)
    assert result.status is ScheduleStatus.OPTIONS_FOUND          # prefer_open never discards
    for o in result.options:
        components = {c.component: c for c in o.choices}
        assert set(components) == {"primary", "required_companion"}   # the lab is still there
        course = _course(o, "01:750:194")
        lab = components["required_companion"].index_number
        assert course.state is AvailabilityState.CLOSED
        assert course.reasons == [f"required_companion_closed:{lab}"]
        assert o.availability.state is AvailabilityState.CLOSED
        issue = next(i for i in o.issues if i.code == "REQUIRED_COMPONENT_CLOSED")
        assert issue.indexes == [lab] and issue.details["component"] == "required_companion"
        assert o.score.closed_sections_if_current == 1
    required = _gen(world, student, ["01:750:194"], mode=AvailabilityMode.REQUIRE_OPEN)
    assert required.status is ScheduleStatus.NO_VALID_SCHEDULE and not required.options
    blocker = next(i for i in required.issues if i.code == "NO_OPEN_SECTION")
    assert blocker.courses == ["01:750:194"] and blocker.details["component"] == \
        "required_companion" and blocker.details["excluded_by"] == {"availability_closed": 8}
    assert not any(i.code == "NO_OPEN_SECTION" and i.details["component"] == "primary"
                   for i in required.issues)
    too_strict = next(i for i in required.issues if i.code == "USER_CONSTRAINTS_TOO_STRICT")
    assert too_strict.details["individually_blocking"] == ["availability"]


def test_regression_lecture_open_lab_closed_is_never_open(world) -> None:
    """The bundle rule's regression: OPEN lecture + CLOSED required lab."""
    _refresh(world, [i for i in _payload() if i != "13384"] + BASE_194)
    for o in _gen(world, _student(world), ["01:750:194"], max_results=25).options:
        primary = next(c for c in o.choices if c.component == "primary")
        assert primary.availability.state is AvailabilityState.OPEN
        assert _course(o, "01:750:194").state is not AvailabilityState.OPEN
        assert Reason.ALL_SECTIONS_OPEN_CURRENT not in o.reasons


def test_d_closed_lecture_closes_the_bundle(world) -> None:
    """Real: 01:750:194 - no lecture open, lab 13384 open."""
    _refresh(world)
    result = _gen(world, _student(world), ["01:750:194"], max_results=25)
    assert result.status is ScheduleStatus.OPTIONS_FOUND
    best = result.options[0]
    lab = next(c for c in best.choices if c.component == "required_companion")
    assert lab.index_number == "13384"              # ranked: the open lab first
    assert best.score.closed_sections_if_current == 1
    course = _course(best, "01:750:194")
    primary = next(c for c in best.choices if c.component == "primary").index_number
    assert course.state is AvailabilityState.CLOSED
    assert course.reasons == [f"primary_closed:{primary}"]
    keys = [(o.score.closed_sections_if_current, o.score.class_days) for o in result.options]
    assert keys[0] == min(keys)
    required = _gen(world, _student(world), ["01:750:194"], mode=AvailabilityMode.REQUIRE_OPEN)
    blocker = next(i for i in required.issues if i.code == "NO_OPEN_SECTION")
    assert blocker.details["component"] == "primary"


def test_e_unknown_lab_makes_the_bundle_unknown(world) -> None:
    """SYNTHETIC: lectures open; the labs' two sources disagree at the same
    moment (courses.json says open, openSections says closed)."""
    for source in world.scalars(select(DataSource).where(DataSource.term_code == FALL26)):
        source.retrieved_at = T0 - timedelta(minutes=5)
    for s in world.scalars(select(CourseSection).where(CourseSection.index_number.in_(
            BASE_194 + LAB_194))):
        s.open_status = True
    world.commit()
    _refresh(world, [i for i in _payload() if i not in LAB_194] + BASE_194)
    result = _gen(world, _student(world), ["01:750:194"], max_results=25)
    for o in result.options:
        course = _course(o, "01:750:194")
        lab = next(c for c in o.choices if c.component == "required_companion")
        assert [c.state for c in course.components] == [AvailabilityState.OPEN,
                                                         AvailabilityState.UNKNOWN]
        assert course.state is AvailabilityState.UNKNOWN
        assert course.reasons == [f"required_companion_unknown:{lab.index_number}"]
        assert o.score.unknown_availability_if_current == 1
    required = _gen(world, _student(world), ["01:750:194"], mode=AvailabilityMode.REQUIRE_OPEN)
    blocker = next(i for i in required.issues if i.code == "NO_OPEN_SECTION")
    assert blocker.details["excluded_by"] == {"availability_unknown": 8}


def test_f_academic_corequisite_keeps_two_bundles(world) -> None:
    """Real: 01:750:229 (2 open) requires 01:750:227 (1 open: 13523) in the
    same term. Two courses, two bundles - the co-requisite is never a
    registration component of 229."""
    _refresh(world)
    result = _gen(world, _student(world), ["01:750:227", "01:750:229"], max_results=25)
    assert result.status is ScheduleStatus.OPTIONS_FOUND
    kinds = {(r.kind, r.course, r.related) for r in result.relationships}
    assert ("academic_corequisite", "01:750:229", "01:750:227") in kinds
    assert not any(k == "registration_component" for k, _, _ in kinds)
    mixed = [o for o in result.options
             if _course(o, "01:750:229").state is AvailabilityState.OPEN
             and _course(o, "01:750:227").state is AvailabilityState.CLOSED]
    assert mixed, "229 open + 227 closed must be shown, not discarded"
    for o in mixed:
        assert [c.course for c in o.availability.courses] == ["01:750:227", "01:750:229"]
        assert all(len(c.components) == 1 and c.components[0].component == "primary"
                   for c in o.availability.courses)
        assert o.availability.state is AvailabilityState.CLOSED and not o.availability.all_open_now
        assert any(i.code == "SECTION_CLOSED" and i.courses == ["01:750:227"] for i in o.issues)
    required = _gen(world, _student(world), ["01:750:227", "01:750:229"],
                    mode=AvailabilityMode.REQUIRE_OPEN)
    for o in required.options:
        assert {c.index_number for c in o.choices if c.course == "01:750:227"} == {"13523"}
        assert o.availability.all_open_now


def test_real_193_everything_closed_is_still_a_valid_schedule(world) -> None:
    """Real: 01:750:193 and its LB lab - 0 of 9 open each."""
    _refresh(world)
    student = _student(world)
    result = _gen(world, student, ["01:750:193"], max_results=5)
    assert result.status is ScheduleStatus.OPTIONS_FOUND
    assert all(o.availability.state is AvailabilityState.CLOSED for o in result.options)
    assert all(o.score.closed_sections_if_current == 2 for o in result.options)
    required = _gen(world, student, ["01:750:193"], mode=AvailabilityMode.REQUIRE_OPEN)
    slots = sorted(i.details["component"] for i in required.issues if i.code == "NO_OPEN_SECTION")
    assert slots == ["primary", "required_companion"]


def test_real_202_older_term_has_no_current_availability(world) -> None:
    """Real: Fall 2025 01:750:202 + LB, never refreshed - require_open refuses."""
    _refresh(world)
    student = _student(world)
    result = _gen(world, student, ["01:750:202"], term=FALL25)
    assert result.metadata.availability_freshness is AvailabilityFreshness.STALE
    assert result.options and all(o.availability.freshness is AvailabilityFreshness.STALE
                                  for o in result.options)
    required = _gen(world, student, ["01:750:202"], term=FALL25,
                    mode=AvailabilityMode.REQUIRE_OPEN)
    assert not required.options
    blocker = next(i for i in required.issues if i.code == "AVAILABILITY_NOT_CURRENT")
    assert blocker.severity.value == "blocker"
    blocked = [i for i in required.issues if i.code == "NO_OPEN_SECTION"]
    assert sorted(i.details["component"] for i in blocked) == ["primary", "required_companion"]
    assert all(set(i.details["excluded_by"]) == {"availability_not_current"} for i in blocked)


def test_prefer_open_ranks_open_first_but_never_hides_closed(world) -> None:
    _refresh(world)
    student = _student(world)
    stale = _gen(world, student, ["01:750:203"], max_results=25, at=T0 + timedelta(hours=1))
    current = _gen(world, student, ["01:750:203"], max_results=25)
    assert len(stale.options) == len(current.options)            # nothing excluded
    first = current.options[0].choices[0]
    assert first.availability.state is AvailabilityState.OPEN
    # Structural verification ranks first (unverified restriction, unknown
    # time), then availability: open before closed within equal structure.
    keys = [(o.score.needs_confirmation, o.score.unknown_time_sections,
             o.score.closed_sections_if_current) for o in current.options]
    assert keys == sorted(keys)
    assert any(k[2] for k in keys) and not keys[0][2]
    assert all(o.score.closed_sections_if_current == 0 for o in stale.options)


# ==========================================================================
# Read-only generation, bounded queries
# ==========================================================================

AVAILABILITY_TABLES = {"availability_refresh", "section_availability",
                       "section_availability_change"}


def _utc(value):
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _hashes(session, tables=None) -> dict[str, str]:
    out = {}
    for table in tables or Base.metadata.sorted_tables:
        rows = sorted(repr(tuple(r)) for r in session.execute(select(table)).all())
        out[table.name] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
    return out


def test_generation_writes_nothing_not_even_availability(world) -> None:
    _refresh(world)
    student = _student(world)
    before = _hashes(world)
    for mode in AvailabilityMode:
        for courses in (["01:750:194"], ["01:750:227", "01:750:229"], ["01:750:193"]):
            _gen(world, student, courses, mode=mode)
        _gen(world, student, ["01:750:194"], mode=mode, at=T0 + timedelta(days=1))
    assert _hashes(world) == before
    assert AVAILABILITY_TABLES <= set(before)


def test_availability_adds_a_fixed_number_of_queries_and_no_network(world) -> None:
    _refresh(world)
    student = _student(world)
    statements: list[str] = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    engine = world.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        _gen(world, student, ["01:750:203"])
        one = len(statements)
        statements.clear()
        _gen(world, student, ["01:750:194", "01:750:227", "01:750:229", "01:750:205"])
        many = len(statements)
        availability = [s for s in statements if "availability" in s]
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert many == one          # 1 course or 4 (with a lab): same number of statements
    # latest accepted refresh + the snapshot rows of the candidates, once each
    assert len(availability) == 2
