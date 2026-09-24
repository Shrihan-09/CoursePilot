"""BM25 index lifecycle: staleness, concurrency and failure (Phase 5.11).

The invariant:

    same search inputs    -> same index, reused
    changed search inputs -> the old index cannot be served

Every staleness test drives a **real database mutation** and asserts the
next search reflects it. One test deliberately bypasses invalidation and
asserts that the stale result appears - so the suite is proven able to catch
the exact bug this phase exists to prevent.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import decimal
import os
import threading
import uuid

import pytest
from sqlalchemy import text

from app.core.metrics import (
    SEARCH_INDEX_BUILD_FAILURES,
    SEARCH_INDEX_BUILDS,
    SEARCH_INDEX_CONCURRENT_SUPPRESSED,
    SEARCH_INDEX_REUSE,
    SEARCH_INDEX_STALE_SERVED,
    SEARCH_INDEX_VERSION_UNAVAILABLE,
    get_metrics,
)
from app.models import SEARCH_TABLES, CatalogCourseEntry, Course, DataSource, Subject
from app.services.search.index_registry import (
    IndexUnavailable,
    SearchIndexRegistry,
    corpus_token,
    get_search_index_registry,
    read_search_version,
)

requires_db = pytest.mark.db


@contextlib.contextmanager
def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    url = os.environ.get(
        "DATABASE_URL_SYNC",
        "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test",
    )
    engine = create_engine(url, poolclass=NullPool)
    try:
        with sessionmaker(engine)() as session:
            yield session
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _fresh():
    get_metrics().reset()
    get_search_index_registry().reset()
    yield
    get_metrics().reset()
    get_search_index_registry().reset()


def _source(session):
    suffix = uuid.uuid4().hex[:8]
    source = DataSource(kind="manual_curation", url=f"synthetic://idx/{suffix}",
                        content_hash=suffix, retrieved_at=dt.datetime.now(dt.UTC),
                        version=1)
    session.add(source)
    session.flush()
    return source


def _make_course(session, *, title, description=None, catalog_year="2026-2027"):
    """A course with a unique identity and an optional catalog description."""
    unit = "01"
    subject_code = uuid.uuid4().hex[:6]
    number = uuid.uuid4().hex[:6]
    source = _source(session)
    subject = Subject(code=subject_code, offering_unit_code=unit,
                      description="Test Subject", source_id=source.id)
    session.add(subject)
    session.flush()
    course = Course(
        offering_unit_code=unit, subject_code=subject_code, course_number=number,
        supplement_code="", course_string=f"{unit}:{subject_code}:{number}",
        title=title, credits=decimal.Decimal("4.0"),
        subject_id=subject.id, source_id=source.id,
    )
    session.add(course)
    session.flush()
    if description:
        session.add(CatalogCourseEntry(
            course_id=course.id, course_string=course.course_string,
            description=description, catalog_year=catalog_year,
            title=title, source_id=source.id,
        ))
        session.flush()
    session.commit()
    return course, subject


def _make_soc_course(session, *, title):
    """A course whose key matches the real Rutgers format.

    The explanation route validates `course_key` against
    the SOC course-key pattern, so the hex identifiers `_make_course` uses for
    corpus tests are rejected with a 422 before the index is ever consulted.
    """
    import random

    source = _source(session)
    unit = "01"
    for _ in range(50):
        subject_code = f"{random.randint(100, 999)}"
        number = f"{random.randint(100, 999)}"
        key = f"{unit}:{subject_code}:{number}"
        exists = session.execute(
            text("SELECT 1 FROM course WHERE course_string = :k"), {"k": key}
        ).first()
        if not exists:
            break
    else:                                            # pragma: no cover
        pytest.skip("could not find a free SOC-format course key")

    subject = session.execute(
        text("SELECT id FROM subject WHERE code = :c AND offering_unit_code = :u"),
        {"c": subject_code, "u": unit},
    ).first()
    if subject is None:
        subject_row = Subject(code=subject_code, offering_unit_code=unit,
                              description="SOC Probe Subject", source_id=source.id)
        session.add(subject_row)
        session.flush()
        subject_id = subject_row.id
    else:
        subject_id = subject[0]

    course = Course(
        offering_unit_code=unit, subject_code=subject_code, course_number=number,
        supplement_code="", course_string=key, title=title,
        credits=decimal.Decimal("4.0"), subject_id=subject_id, source_id=source.id,
    )
    session.add(course)
    session.flush()
    session.commit()
    return course


def _search(registry, session, query, limit=10):
    from app.services.search.synonyms import ExpandingSearcher

    index = registry.get(session)
    return ExpandingSearcher(index.searcher).search(query, limit=limit)


def _keys(results):
    return [r.course_key for r in results]


# ==========================================================================
# the version signal itself
# ==========================================================================


@requires_db
def test_every_search_table_has_a_trigger() -> None:
    """The declared list and the installed triggers must not drift."""
    with _session() as session:
        installed = {
            row[0]
            for row in session.execute(text(
                "SELECT c.relname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE t.tgname = 'trg_search_version_bump' "
                "AND NOT t.tgisinternal"
            )).all()
        }
    assert installed == set(SEARCH_TABLES)


@requires_db
def test_the_search_version_is_independent_of_the_rules_version() -> None:
    """Two independent inputs, two counters.

    A requirement recuration must not trigger a 264 ms BM25 rebuild, and a
    course title fix must not invalidate every cached audit.
    """
    with _session() as session:
        before_search = read_search_version(session)
        before_rules = session.execute(
            text("SELECT version FROM rules_version WHERE id = 1")).scalar()

        # A search-side change.
        _make_course(session, title="Independence Probe")

        after_search = read_search_version(session)
        after_rules = session.execute(
            text("SELECT version FROM rules_version WHERE id = 1")).scalar()

    assert after_search > before_search, "search version must move"
    assert after_rules == before_rules, "a course change must not touch rules"


@requires_db
@pytest.mark.parametrize("table", SEARCH_TABLES)
def test_raw_sql_mutations_bump_the_search_version(table) -> None:
    """Raw SQL, because ingestion and hand fixes do not go through the ORM."""
    column = {"course": "title", "subject": "description",
              "catalog_course_entry": "description"}[table]
    with _session() as session:
        _make_course(session, title="Raw SQL Probe", description="a description")
        before = read_search_version(session)
        session.execute(text(f'UPDATE "{table}" SET {column} = {column}'))
        session.commit()
        assert read_search_version(session) > before


@requires_db
def test_unrelated_tables_do_not_bump_the_search_version() -> None:
    """Rebuilding a 264 ms index on an unrelated write would be a bug."""
    with _session() as session:
        _make_course(session, title="Unrelated Probe")
        before = read_search_version(session)
        session.execute(text(
            "INSERT INTO user_account (id, identity_provider, external_subject, "
            "is_admin) VALUES (gen_random_uuid(), 'oidc', "
            "'idx-' || gen_random_uuid(), false)"))
        session.commit()
        assert read_search_version(session) == before


# ==========================================================================
# reuse - the point of the phase
# ==========================================================================


@requires_db
def test_an_unchanged_corpus_is_built_once_and_then_reused() -> None:
    registry = SearchIndexRegistry()
    metrics = get_metrics()
    with _session() as session:
        _make_course(session, title="Reuse Probe")

        first = registry.get(session)
        assert metrics.counter(SEARCH_INDEX_BUILDS) == 1

        for _ in range(10):
            again = registry.get(session)
            assert again is first, "the same object must be handed back"

    assert metrics.counter(SEARCH_INDEX_BUILDS) == 1
    assert metrics.counter(SEARCH_INDEX_REUSE) == 10


@requires_db
def test_the_published_index_is_immutable_and_swapped_atomically() -> None:
    registry = SearchIndexRegistry()
    with _session() as session:
        _make_course(session, title="Atomic Probe")
        first = registry.get(session)
        held = first.searcher  # a reader holding the old index

        _make_course(session, title="Atomic Probe Two")
        second = registry.get(session)

    assert second is not first
    assert second.version != first.version
    # The old object is untouched - a reader mid-request still sees a whole,
    # consistent index rather than a half-rebuilt one.
    assert first.searcher is held
    assert first.document_count >= 1
    with pytest.raises(Exception):
        first.version = "mutated"  # frozen dataclass


# ==========================================================================
# Part 8 - staleness, driven by real mutations
# ==========================================================================


@requires_db
def test_1_a_title_change_is_reflected() -> None:
    marker = f"quantumlepton{uuid.uuid4().hex[:6]}"
    registry = SearchIndexRegistry()
    with _session() as session:
        course, _ = _make_course(session, title="Ordinary Title")
        assert _keys(_search(registry, session, marker)) == []

        session.execute(text("UPDATE course SET title = :t WHERE id = :i"),
                        {"t": f"{marker} Studies", "i": course.id})
        session.commit()

        assert course.course_string in _keys(_search(registry, session, marker))


@requires_db
def test_2_a_description_change_is_reflected() -> None:
    marker = f"photosynthase{uuid.uuid4().hex[:6]}"
    registry = SearchIndexRegistry()
    with _session() as session:
        course, _ = _make_course(session, title="Desc Probe",
                                 description="original description text")
        assert _keys(_search(registry, session, marker)) == []

        session.execute(
            text("UPDATE catalog_course_entry SET description = :d "
                 "WHERE course_id = :i"),
            {"d": f"now mentions {marker}", "i": course.id})
        session.commit()

        assert course.course_string in _keys(_search(registry, session, marker))


@requires_db
def test_3_a_catalog_year_change_is_reflected_in_provenance() -> None:
    registry = SearchIndexRegistry()
    with _session() as session:
        course, _ = _make_course(session, title="Year Probe",
                                 description="year probe description",
                                 catalog_year="2026-2027")
        index = registry.get(session)
        document = index.documents_by_key[course.course_string]
        assert document.description_provenance.catalog_year == "2026-2027"

        session.execute(
            text("UPDATE catalog_course_entry SET catalog_year = '2027-2028' "
                 "WHERE course_id = :i"), {"i": course.id})
        session.commit()

        rebuilt = registry.get(session)
        assert rebuilt is not index
        assert (rebuilt.documents_by_key[course.course_string]
                .description_provenance.catalog_year == "2027-2028")


@requires_db
def test_4_course_insertion_is_reflected() -> None:
    marker = f"tribolumen{uuid.uuid4().hex[:6]}"
    registry = SearchIndexRegistry()
    with _session() as session:
        _make_course(session, title="Existing Course")
        before = registry.get(session).document_count
        assert _keys(_search(registry, session, marker)) == []

        new_course, _ = _make_course(session, title=f"{marker} Seminar")

        assert new_course.course_string in _keys(_search(registry, session, marker))
        assert registry.get(session).document_count == before + 1


@requires_db
def test_5_course_deletion_is_reflected() -> None:
    marker = f"ephemeropt{uuid.uuid4().hex[:6]}"
    registry = SearchIndexRegistry()
    with _session() as session:
        course, _ = _make_course(session, title=f"{marker} Seminar")
        # Captured before the delete: afterwards the ORM instance is expired
        # and touching an attribute raises ObjectDeletedError.
        key, course_id = course.course_string, course.id
        assert key in _keys(_search(registry, session, marker))

        session.execute(text("DELETE FROM catalog_course_entry WHERE course_id = :i"),
                        {"i": course_id})
        session.execute(text("DELETE FROM course WHERE id = :i"), {"i": course_id})
        session.commit()

        assert key not in _keys(_search(registry, session, marker))


@requires_db
def test_6_a_subject_description_change_is_reflected() -> None:
    """subject.description becomes CourseDocument.subject_name - a searched
    field, so it is corpus input even though it lives on another table."""
    marker = f"astrobiolog{uuid.uuid4().hex[:6]}"
    registry = SearchIndexRegistry()
    with _session() as session:
        course, subject = _make_course(session, title="Subject Probe")
        assert _keys(_search(registry, session, marker)) == []

        session.execute(text("UPDATE subject SET description = :d WHERE id = :i"),
                        {"d": f"{marker} Department", "i": subject.id})
        session.commit()

        assert course.course_string in _keys(_search(registry, session, marker))


@requires_db
def test_7_curated_expansion_changes_change_the_index_identity() -> None:
    """The expansions live in code, so no database trigger can see them.

    The identity carries a code half for exactly this reason; the count is a
    tripwire for add/remove, and CORPUS_CODE_VERSION covers edits.
    """
    import app.services.search.index_registry as registry_module
    from app.services.search.synonyms import CURATED_EXPANSIONS, Expansion

    with _session() as session:
        before = corpus_token(session)

        extra = CURATED_EXPANSIONS + (
            Expansion(term="zzz", expands_to=("z",), note="test"),
        )
        import app.services.search.synonyms as synonyms_module

        synonyms_module.CURATED_EXPANSIONS = extra
        try:
            assert corpus_token(session) != before
        finally:
            synonyms_module.CURATED_EXPANSIONS = CURATED_EXPANSIONS
        assert corpus_token(session) == before

        # And the explicit half.
        bumped = registry_module.CORPUS_CODE_VERSION
        registry_module.CORPUS_CODE_VERSION = bumped + "-next"
        try:
            assert corpus_token(session) != before
        finally:
            registry_module.CORPUS_CODE_VERSION = bumped


@requires_db
def test_8_a_bulk_ingestion_style_replacement_is_reflected() -> None:
    """The real ingestion boundary: many rows in one statement."""
    marker = f"bulkreplace{uuid.uuid4().hex[:6]}"
    registry = SearchIndexRegistry()
    with _session() as session:
        courses = [_make_course(session, title=f"Bulk {i}")[0] for i in range(3)]
        registry.get(session)
        before_version = read_search_version(session)

        ids = [str(c.id) for c in courses]
        session.execute(
            text("UPDATE course SET title = :t WHERE id::text = ANY(:ids)"),
            {"t": f"{marker} Replaced", "ids": ids})
        session.commit()

        # One statement, one bump - not one per row.
        assert read_search_version(session) == before_version + 1
        found = _keys(_search(registry, session, marker, limit=10))
        for course in courses:
            assert course.course_string in found


@requires_db
def test_9_a_process_restart_rebuilds_from_the_database() -> None:
    """A fresh registry is what a restarted worker has."""
    marker = f"restartprobe{uuid.uuid4().hex[:6]}"
    with _session() as session:
        course, _ = _make_course(session, title=f"{marker} Course")
        first = SearchIndexRegistry()
        assert course.course_string in _keys(_search(first, session, marker))

        restarted = SearchIndexRegistry()          # simulates a new process
        assert restarted.published is None
        assert course.course_string in _keys(_search(restarted, session, marker))


# ==========================================================================
# the mutation test: prove the suite catches the bug
# ==========================================================================


@requires_db
def test_bypassing_invalidation_serves_a_stale_result() -> None:
    """Deliberately defeat invalidation and assert staleness APPEARS.

    A staleness suite that has never seen a stale result is a suite that
    might be asserting nothing. This pins that the tests above are load
    bearing: with the version signal frozen, a title change becomes
    invisible - exactly the bug this phase exists to prevent.
    """
    marker = f"staleproof{uuid.uuid4().hex[:6]}"
    import app.services.search.index_registry as rm

    with _session() as session:
        course, _ = _make_course(session, title=f"{marker} Original")
        key, course_id = course.course_string, course.id

        # --- 1. working invalidation: the change IS picked up -------------
        working = SearchIndexRegistry()
        assert key in _keys(_search(working, session, marker))
        session.execute(text("UPDATE course SET title = :t WHERE id = :i"),
                        {"t": "Renamed Away", "i": course_id})
        session.commit()
        assert key not in _keys(_search(working, session, marker)), (
            "with invalidation working, the rename must be visible"
        )

        # --- 2. frozen signal: the SAME change is now invisible -----------
        # Frozen BEFORE the first build, so the published index carries the
        # frozen token and no later mutation can ever appear to move it.
        session.execute(text("UPDATE course SET title = :t WHERE id = :i"),
                        {"t": f"{marker} Original", "i": course_id})
        session.commit()

        broken = SearchIndexRegistry()
        original = rm.read_search_version
        rm.read_search_version = lambda _s: 1       # a trigger that never fires
        try:
            assert key in _keys(_search(broken, session, marker))

            session.execute(text("UPDATE course SET title = :t WHERE id = :i"),
                            {"t": "Renamed Away Again", "i": course_id})
            session.commit()

            stale = _keys(_search(broken, session, marker))
            assert key in stale, (
                "expected the frozen-signal index to still match the OLD title - "
                "if this passes, the staleness tests above are not load bearing"
            )
        finally:
            rm.read_search_version = original


# ==========================================================================
# Part 7 - concurrency
# ==========================================================================


@requires_db
@pytest.mark.parametrize("threads", [1, 2, 10, 50])
def test_concurrent_cold_starts_build_exactly_one_index(threads) -> None:
    """N simultaneous first requests must not build N indexes."""
    registry = SearchIndexRegistry()
    metrics = get_metrics()
    with _session() as session:
        _make_course(session, title="Concurrency Probe")

    results: list[object] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(threads)

    def worker() -> None:
        try:
            with _session() as session:
                barrier.wait(timeout=30)           # maximise the collision
                index = registry.get(session)
                results.append(index)
        except Exception as exc:                    # pragma: no cover
            errors.append(exc)

    workers = [threading.Thread(target=worker) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert errors == [], f"race exceptions: {errors}"
    assert len(results) == threads
    assert metrics.counter(SEARCH_INDEX_BUILDS) == 1, "more than one build"
    # Every caller got the SAME object - no partial index was ever visible.
    assert len({id(r) for r in results}) == 1


@requires_db
def test_concurrent_callers_all_see_identical_rankings() -> None:
    registry = SearchIndexRegistry()
    with _session() as session:
        _make_course(session, title="Ranking Probe Alpha")
        _make_course(session, title="Ranking Probe Beta")

    rankings: list[tuple[str, ...]] = []
    barrier = threading.Barrier(12)

    def worker() -> None:
        with _session() as session:
            barrier.wait(timeout=30)
            rankings.append(tuple(_keys(_search(registry, session, "ranking probe"))))

    workers = [threading.Thread(target=worker) for _ in range(12)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert len(rankings) == 12
    assert len(set(rankings)) == 1, "concurrent callers disagreed on ranking"


# ==========================================================================
# Part 9 - failure safety
# ==========================================================================


@requires_db
def test_a_failed_rebuild_keeps_the_known_good_index_and_says_so() -> None:
    """A failed rebuild must never replace a good index with a broken one -
    and must not pretend the old one is current."""
    registry = SearchIndexRegistry()
    metrics = get_metrics()
    with _session() as session:
        course, _ = _make_course(session, title="Failure Probe")
        good = registry.get(session)

        # Move the corpus so a rebuild is required.
        _make_course(session, title="Failure Probe Two")

        import app.services.search.index_registry as rm

        original = rm.build_course_documents
        rm.build_course_documents = lambda s: (_ for _ in ()).throw(
            RuntimeError("corpus build exploded"))
        try:
            served = registry.get(session)
        finally:
            rm.build_course_documents = original

    assert served is good, "the known-good index must survive a failed rebuild"
    assert metrics.counter(SEARCH_INDEX_BUILD_FAILURES) == 1
    # Loudly, not silently: the counter records that a known-old index was
    # served because the corpus had moved.
    assert metrics.counter(SEARCH_INDEX_STALE_SERVED) == 1


@requires_db
def test_a_failed_first_build_raises_rather_than_publishing_nothing() -> None:
    registry = SearchIndexRegistry()
    with _session() as session:
        import app.services.search.index_registry as rm

        original = rm.build_course_documents
        rm.build_course_documents = lambda s: (_ for _ in ()).throw(
            RuntimeError("corpus build exploded"))
        try:
            with pytest.raises(IndexUnavailable):
                registry.get(session)
        finally:
            rm.build_course_documents = original

    assert registry.published is None, "nothing broken may be published"


@requires_db
def test_an_empty_corpus_is_publishable_and_searchable() -> None:
    """Empty is a legitimate state, not a failure - a fresh database has it."""
    registry = SearchIndexRegistry()
    with _session() as session:
        import app.services.search.index_registry as rm

        original = rm.build_course_documents
        rm.build_course_documents = lambda s: []
        try:
            index = registry.get(session)
        finally:
            rm.build_course_documents = original

    assert index.document_count == 0
    from app.services.search.synonyms import ExpandingSearcher

    assert ExpandingSearcher(index.searcher).search("anything", limit=5) == []


@requires_db
def test_a_missing_version_table_falls_back_to_building_per_request() -> None:
    """Pre-5.11 behaviour: slower, and equally correct. Never silent reuse
    of an index whose freshness cannot be proven."""
    registry = SearchIndexRegistry()
    metrics = get_metrics()

    def rename(frm, to):
        with _session() as admin:
            admin.execute(text(f"ALTER TABLE {frm} RENAME TO {to}"))
            admin.commit()

    with _session() as session:
        _make_course(session, title="No Version Table Probe")

    rename("search_version", "search_version_hidden")
    try:
        with _session() as session:
            assert corpus_token(session) is None
            first = registry.get(session)
            second = registry.get(session)
            assert first is not second, "must rebuild when freshness is unprovable"
    finally:
        rename("search_version_hidden", "search_version")

    assert metrics.counter(SEARCH_INDEX_VERSION_UNAVAILABLE) == 2
    assert metrics.counter(SEARCH_INDEX_BUILDS) == 2
    assert registry.published is None, "an unverified index must not be published"


@requires_db
def test_a_failed_version_read_leaves_the_session_usable() -> None:
    """The Phase 5.7 lesson: a failed statement aborts a PostgreSQL
    transaction, and the corpus build queries the same session next."""
    with _session() as session:
        session.execute(text("ALTER TABLE search_version RENAME TO sv_hidden"))
        session.commit()
        try:
            assert read_search_version(session) is None
            assert session.execute(text("SELECT 1")).scalar() == 1
        finally:
            session.execute(text("ALTER TABLE sv_hidden RENAME TO search_version"))
            session.commit()


# ==========================================================================
# derived data
# ==========================================================================


@requires_db
def test_the_index_is_derived_and_can_always_be_thrown_away() -> None:
    registry = SearchIndexRegistry()
    marker = "derivedprobe"
    with _session() as session:
        course, _ = _make_course(session, title=f"{marker} Course")
        before = _keys(_search(registry, session, marker))

        registry.reset()
        assert registry.published is None

        after = _keys(_search(registry, session, marker))

    assert after == before
    assert course.course_string in after


# ==========================================================================
# Part 10/18 - the headline claim, proven by instrumentation
# ==========================================================================


@requires_db
async def test_the_explanation_endpoint_does_not_rebuild_the_index() -> None:
    """**The question this phase exists to answer.**

    Proven with counters, not timing: timing shows the endpoint got faster,
    which is compatible with the index still being rebuilt on a faster
    machine. The counter shows the build did not happen.
    """
    import uuid as _uuid

    from httpx import ASGITransport, AsyncClient

    from app.api.security import Principal, get_principal
    from app.main import create_app
    from app.models import Student, UserAccount

    with _session() as session:
        course = _make_soc_course(session, title="Endpoint Probe Course")
        account = UserAccount(identity_provider="oidc",
                              external_subject=f"idx-{_uuid.uuid4()}")
        session.add(account)
        session.flush()
        student = session.scalars(
            __import__("sqlalchemy").select(Student)).first()
        if student is None:
            pytest.skip("no student in the test database")
        previous_owner = student.user_id
        student.user_id = account.id
        session.commit()
        account_id, student_id = account.id, student.id
        course_key = course.course_string

    get_search_index_registry().reset()
    metrics = get_metrics()
    metrics.reset()

    app = create_app()
    app.dependency_overrides[get_principal] = lambda: Principal(
        account_id=account_id, subject="s", issuer="i", provider="oidc")

    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as ac:
            statuses = []
            for _ in range(6):
                response = await ac.post(
                    "/api/v1/explanations/recommendation",
                    json={"course_key": course_key,
                          "explanation_type": "why_recommended"},
                )
                statuses.append(response.status_code)
    finally:
        with _session() as session:
            session.execute(
                text("UPDATE student SET user_id = :u WHERE id = :i"),
                {"u": previous_owner, "i": student_id})
            session.commit()

    assert all(s == 200 for s in statuses), statuses

    builds = metrics.counter(SEARCH_INDEX_BUILDS)
    reuse = metrics.counter(SEARCH_INDEX_REUSE)
    assert builds == 1, f"the index was built {builds} times across 6 requests"
    assert reuse == 5, f"expected 5 reuses, saw {reuse}"


@requires_db
def test_the_explanation_route_no_longer_constructs_an_index_itself() -> None:
    """Static guard: the per-request build must not creep back in.

    A counter test only covers the path it exercises; this covers the code.
    """
    import ast
    import pathlib

    route = (pathlib.Path(__file__).resolve().parents[1]
             / "app" / "api" / "v1" / "routes" / "explanations.py")
    tree = ast.parse(route.read_text(encoding="utf-8"))

    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)

    # `build_course_documents` must be gone entirely. `build_bm25` survives
    # only in the degraded no-retrieval fallback, where the corpus is empty
    # and the call is free.
    assert "build_course_documents" not in called, (
        "the explanation route builds the corpus per request again"
    )
