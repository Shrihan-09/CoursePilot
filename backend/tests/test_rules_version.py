"""Database-maintained rules versioning (Phase 5.8, Parts 10-13).

The claim being tested is a **schema contract**, not an application one:

> Any database mutation capable of changing Degree Engine rule inputs also
> changes `rules_version.version`.

So the tests mutate through raw SQL wherever possible. Calling an
application helper and then asserting the version moved would prove only
that the helper works; the whole reason for choosing a trigger over an ORM
hook is that writes arrive by paths the application never sees.

PostgreSQL only, and labelled as such. SQLite has no equivalent here and a
fake abstraction would claim a guarantee that was never tested against
production semantics - the application falls back to the Phase 5.7
fingerprint there instead.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import uuid

import pytest
from sqlalchemy import text

from app.models import RULES_TABLES

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


def _version(session) -> int:
    return session.execute(
        text("SELECT version FROM rules_version WHERE id = 1")
    ).scalar()


@contextlib.contextmanager
def _bumped(session, *, expected: bool = True):
    """Assert the rules version did (or did not) move across a block."""
    before = _version(session)
    yield
    session.commit()
    after = _version(session)
    if expected:
        assert after > before, "rules version did not change"
    else:
        assert after == before, f"rules version changed unexpectedly ({before}->{after})"


# --------------------------------------------------------------------------
# a minimal rules graph, built with raw SQL so nothing depends on the ORM
# --------------------------------------------------------------------------


def _raw_scenario(session) -> dict:
    suffix = uuid.uuid4().hex[:8]
    ids = {k: uuid.uuid4() for k in
           ("source", "school", "program", "version", "requirement", "option",
            "rule", "subject", "course")}
    session.execute(
        text("INSERT INTO data_source (id, kind, url, content_hash, "
             "retrieved_at, version) "
             "VALUES (:i, 'manual_curation', :u, :h, :t, 1)"),
        {"i": ids["source"], "u": f"synthetic://rv/{suffix}", "h": suffix,
         "t": dt.datetime.now(dt.UTC)},
    )
    session.execute(
        text("INSERT INTO school (id, code, name, campus_code, source_id) "
             "VALUES (:i, :c, 'S', 'NB', :s)"),
        {"i": ids["school"], "c": f"S{suffix}", "s": ids["source"]},
    )
    session.execute(
        text("INSERT INTO program (id, school_id, code, name, degree_type, source_id) "
             "VALUES (:i, :sc, :c, 'P', 'BA', :s)"),
        {"i": ids["program"], "sc": ids["school"], "c": suffix, "s": ids["source"]},
    )
    session.execute(
        text("INSERT INTO program_version (id, program_id, catalog_year, "
             "sharing_policy, curation_status, source_id) "
             "VALUES (:i, :p, '2026-2027', 'exclusive', 'unverified', :s)"),
        {"i": ids["version"], "p": ids["program"], "s": ids["source"]},
    )
    session.execute(
        text("INSERT INTO requirement (id, program_version_id, code, name, "
             "requirement_type, requirement_system, sort_order, min_count, "
             "curation_status, source_id) "
             "VALUES (:i, :v, :c, 'R', 'choose_n', 'major', 1, 2, "
             "'unverified', :s)"),
        {"i": ids["requirement"], "v": ids["version"], "c": f"REQ_{suffix}",
         "s": ids["source"]},
    )
    session.execute(
        text("INSERT INTO subject (id, code, description, offering_unit_code, source_id) "
             "VALUES (:i, :c, 'S', '01', :s)"),
        {"i": ids["subject"], "c": suffix[:6], "s": ids["source"]},
    )
    session.execute(
        text("INSERT INTO course (id, offering_unit_code, subject_code, "
             "course_number, supplement_code, course_string, title, credits, "
             "subject_id, source_id) "
             "VALUES (:i, '01', :sc, :n, '', :cs, 'C', 4.0, :su, :s)"),
        {"i": ids["course"], "sc": suffix[:6], "n": suffix[:6],
         "cs": f"01:{suffix[:6]}:{suffix[:6]}", "su": ids["subject"],
         "s": ids["source"]},
    )
    session.execute(
        text("INSERT INTO requirement_course_option (id, requirement_id, "
             "course_id, source_id) VALUES (:i, :r, :c, :s)"),
        {"i": ids["option"], "r": ids["requirement"], "c": ids["course"],
         "s": ids["source"]},
    )
    session.execute(
        text("INSERT INTO program_rule (id, program_version_id, code, name, "
             "rule_type, is_evaluable, curation_status, source_id) "
             "VALUES (:i, :v, :c, 'Rule', 'course_exclusion', true, "
             "'unverified', :s)"),
        {"i": ids["rule"], "v": ids["version"], "c": f"RULE_{suffix}",
         "s": ids["source"]},
    )
    session.commit()
    return ids


# ==========================================================================
# the triggers exist and match the declared list
# ==========================================================================


@requires_db
def test_every_declared_rules_table_has_a_trigger() -> None:
    """The declared list and the installed triggers must not drift.

    Adding a table to RULES_TABLES without a migration, or dropping a
    trigger without updating the list, fails here rather than silently
    serving stale audits.
    """
    with _session() as session:
        installed = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT c.relname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "WHERE t.tgname = 'trg_rules_version_bump' "
                    "AND NOT t.tgisinternal"
                )
            ).all()
        }
    assert installed == set(RULES_TABLES), (
        f"declared {set(RULES_TABLES)} but installed {installed}"
    )


@requires_db
def test_the_version_table_holds_exactly_one_row() -> None:
    from sqlalchemy.exc import IntegrityError

    with _session() as session:
        assert session.execute(
            text("SELECT count(*) FROM rules_version")
        ).scalar() == 1
        with pytest.raises(IntegrityError):
            session.execute(
                text("INSERT INTO rules_version (id, version) VALUES (2, 1)")
            )
        session.rollback()


# ==========================================================================
# raw SQL - the case an ORM hook would miss (Part 11)
# ==========================================================================


#: A column each rules table actually has, so the UPDATE is valid SQL.
#: Self-assignment: the point is that the STATEMENT bumps the version, not
#: that the value changed - a no-op UPDATE is still a write.
_UPDATABLE_COLUMN = {
    "program": "name",
    "program_version": "curation_status",
    "requirement": "curation_status",
    "requirement_course_option": "category",
    "program_rule": "curation_status",
}


@requires_db
@pytest.mark.parametrize("table", RULES_TABLES)
def test_raw_sql_update_bumps_the_version(table) -> None:
    """The scenario Phase 5.7 rejected MAX(updated_at) over: a catalog fix
    applied directly in psql, which no ORM hook would ever see."""
    column = _UPDATABLE_COLUMN[table]
    with _session() as session:
        _raw_scenario(session)
        with _bumped(session):
            # No WHERE: a blunt statement, exactly like a hurried fix.
            session.execute(text(f'UPDATE "{table}" SET {column} = {column}'))


@requires_db
def test_raw_sql_insert_bumps_the_version() -> None:
    """A newly inserted requirement changes an audit."""
    with _session() as session:
        ids = _raw_scenario(session)
        with _bumped(session):
            session.execute(
                text("INSERT INTO requirement (id, program_version_id, code, "
                     "name, requirement_type, requirement_system, sort_order, "
                     "min_count, curation_status, source_id) "
                     "VALUES (:i, :v, :c, 'R2', 'choose_n', 'major', 2, 1, "
                     "'unverified', :s)"),
                {"i": uuid.uuid4(), "v": ids["version"],
                 "c": f"REQ2_{uuid.uuid4().hex[:8]}", "s": ids["source"]},
            )


@requires_db
def test_raw_sql_delete_bumps_the_version() -> None:
    """A deleted eligibility row changes an audit."""
    with _session() as session:
        ids = _raw_scenario(session)
        with _bumped(session):
            session.execute(
                text("DELETE FROM requirement_course_option WHERE id = :i"),
                {"i": ids["option"]},
            )


@requires_db
def test_raw_sql_program_rename_bumps_the_version() -> None:
    """The Phase 5.7 gap this phase found: program_name is a field of
    DegreeAuditResult, and the old fingerprint never hashed the table."""
    with _session() as session:
        ids = _raw_scenario(session)
        with _bumped(session):
            session.execute(
                text("UPDATE program SET name = 'Renamed' WHERE id = :i"),
                {"i": ids["program"]},
            )


# ==========================================================================
# bulk operations (Part 13)
# ==========================================================================


@requires_db
def test_bulk_update_bumps_once_not_per_row() -> None:
    """Statement-level, so recuration of 800 eligibility rows costs one bump.

    Row-level triggers would be correct too, and 800x more expensive.
    """
    with _session() as session:
        ids = _raw_scenario(session)
        for _ in range(4):
            session.execute(
                text("INSERT INTO requirement (id, program_version_id, code, "
                     "name, requirement_type, requirement_system, sort_order, "
                     "min_count, curation_status, source_id) "
                     "VALUES (:i, :v, :c, 'R', 'choose_n', 'major', 1, 1, "
                     "'unverified', :s)"),
                {"i": uuid.uuid4(), "v": ids["version"],
                 "c": f"B_{uuid.uuid4().hex[:8]}", "s": ids["source"]},
            )
        session.commit()

        before = _version(session)
        session.execute(
            text("UPDATE requirement SET min_count = 3 WHERE program_version_id = :v"),
            {"v": ids["version"]},
        )
        session.commit()
        assert _version(session) == before + 1


@requires_db
def test_bulk_delete_bumps_the_version() -> None:
    with _session() as session:
        ids = _raw_scenario(session)
        with _bumped(session):
            session.execute(
                text("DELETE FROM requirement_course_option "
                     "WHERE requirement_id = :r"),
                {"r": ids["requirement"]},
            )


@requires_db
def test_a_statement_matching_zero_rows_still_bumps() -> None:
    """Documented over-invalidation. A spurious miss costs a recomputation;
    a missed bump costs a student a wrong degree status."""
    with _session() as session:
        _raw_scenario(session)
        with _bumped(session):
            session.execute(
                text("UPDATE requirement SET min_count = 9 WHERE id = :i"),
                {"i": uuid.uuid4()},
            )


# ==========================================================================
# ORM writes (Part 12)
# ==========================================================================


@requires_db
def test_orm_insert_update_delete_bump_the_version() -> None:
    """The mechanism must not care which client wrote."""
    from app.models import DataSource, Program, School

    with _session() as session:
        suffix = uuid.uuid4().hex[:8]
        source = DataSource(
            kind="manual_curation", url=f"synthetic://orm/{suffix}",
            content_hash=suffix, retrieved_at=dt.datetime.now(dt.UTC),
        )
        session.add(source)
        session.flush()
        school = School(code=f"S{suffix}", name="S", campus_code="NB",
                        source_id=source.id)
        session.add(school)
        session.flush()
        session.commit()

        with _bumped(session):
            program = Program(school_id=school.id, code=suffix, name="P",
                              degree_type="BA", source_id=source.id)
            session.add(program)
            session.flush()

        with _bumped(session):
            program.name = "P2"
            session.flush()

        with _bumped(session):
            session.delete(program)
            session.flush()


# ==========================================================================
# unrelated tables must NOT bump
# ==========================================================================


@requires_db
@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO user_account (id, identity_provider, external_subject, is_admin) "
        "VALUES (gen_random_uuid(), 'oidc', 'rv-' || gen_random_uuid(), false)",
        "UPDATE student SET catalog_year = catalog_year WHERE false",
    ],
    ids=["user_account insert", "student update"],
)
def test_non_rules_tables_do_not_bump(sql) -> None:
    """Student state belongs to the academic fingerprint, not the rules
    version. Bumping here would invalidate every student's cache whenever
    anyone signed in."""
    with _session() as session:
        with _bumped(session, expected=False):
            session.execute(text(sql))


@requires_db
def test_reading_does_not_bump() -> None:
    with _session() as session:
        _raw_scenario(session)
        with _bumped(session, expected=False):
            session.execute(text("SELECT count(*) FROM requirement")).scalar()


# ==========================================================================
# the token the cache actually uses
# ==========================================================================


@requires_db
def test_rules_token_uses_the_version_when_available() -> None:
    from app.services.audit.rules_state import VERSION_PREFIX, rules_token

    with _session() as session:
        ids = _raw_scenario(session)
        token = rules_token(session, ids["version"])
        assert token.startswith(VERSION_PREFIX)

        session.execute(
            text("UPDATE requirement SET min_count = 7 WHERE id = :i"),
            {"i": ids["requirement"]},
        )
        session.commit()
        assert rules_token(session, ids["version"]) != token


@contextlib.contextmanager
def _without_versioning():
    """Remove the triggers AND the table, the way a downgrade does.

    The two must go together: the trigger function updates `rules_version`,
    so removing only the table would make every write to a rules table fail
    - see `test_removing_only_the_version_table_fails_writes_loudly`, which
    pins that behaviour deliberately.
    """
    with _session() as admin:
        for table in RULES_TABLES:
            admin.execute(
                text(f'DROP TRIGGER IF EXISTS trg_rules_version_bump ON "{table}"')
            )
        admin.execute(text("ALTER TABLE rules_version RENAME TO rules_version_hidden"))
        admin.commit()
    try:
        yield
    finally:
        with _session() as admin:
            admin.execute(
                text("ALTER TABLE rules_version_hidden RENAME TO rules_version")
            )
            for table in RULES_TABLES:
                admin.execute(
                    text(
                        f"CREATE TRIGGER trg_rules_version_bump "
                        f'AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON "{table}" '
                        f"FOR EACH STATEMENT EXECUTE FUNCTION bump_rules_version()"
                    )
                )
            admin.commit()


@requires_db
def test_rules_token_falls_back_to_the_fingerprint() -> None:
    """Without the version table the token must still be CORRECT, merely
    slower. Falling back to 'assume unchanged' is not offered."""
    from app.services.audit.rules_state import FINGERPRINT_PREFIX, rules_token

    with _session() as session:
        ids = _raw_scenario(session)

    with _without_versioning():
        with _session() as session:
            token = rules_token(session, ids["version"])
            assert token.startswith(FINGERPRINT_PREFIX)

            # And it still detects a change, which is the whole point: the
            # fallback is the Phase 5.7 mechanism, not a degraded one.
            session.execute(
                text("UPDATE requirement SET min_count = 5 WHERE id = :i"),
                {"i": ids["requirement"]},
            )
            session.commit()
            assert rules_token(session, ids["version"]) != token


@requires_db
def test_removing_only_the_version_table_fails_writes_loudly() -> None:
    """A deliberate, documented property: the trigger hard-depends on the
    version table.

    Dropping `rules_version` while its triggers remain makes every write to
    a rules table FAIL. That is the right direction to fail - the alternative
    is rule writes silently going unversioned, which is exactly the stale
    audit this whole mechanism exists to prevent. Operationally it means the
    table and its triggers are removed together, which is what the migration
    downgrade does.
    """
    from sqlalchemy.exc import ProgrammingError

    with _session() as session:
        ids = _raw_scenario(session)

    with _session() as admin:
        admin.execute(text("ALTER TABLE rules_version RENAME TO rules_version_hidden"))
        admin.commit()
    try:
        with _session() as session:
            with pytest.raises(ProgrammingError):
                session.execute(
                    text("UPDATE requirement SET min_count = 4 WHERE id = :i"),
                    {"i": ids["requirement"]},
                )
            session.rollback()
    finally:
        with _session() as admin:
            admin.execute(
                text("ALTER TABLE rules_version_hidden RENAME TO rules_version")
            )
            admin.commit()


@requires_db
def test_version_and_fingerprint_tokens_can_never_be_confused() -> None:
    """A row written under one mechanism must not be read as the other."""
    from app.services.audit.rules_state import FINGERPRINT_PREFIX, VERSION_PREFIX

    assert VERSION_PREFIX != FINGERPRINT_PREFIX
    assert not VERSION_PREFIX.startswith(FINGERPRINT_PREFIX)
    assert not FINGERPRINT_PREFIX.startswith(VERSION_PREFIX)


@requires_db
def test_a_failed_version_read_leaves_the_session_usable() -> None:
    """Phase 5.7's lesson, applied to the new lookup: a failed statement
    aborts a PostgreSQL transaction, and the fingerprint fallback queries the
    same session immediately afterwards."""
    from app.services.audit.rules_state import read_rules_version

    with _session() as session:
        ids = _raw_scenario(session)
        session.execute(text("ALTER TABLE rules_version RENAME TO rv_hidden"))
        session.commit()
        try:
            assert read_rules_version(session) is None
            # The session must still work.
            assert session.execute(text("SELECT 1")).scalar() == 1
        finally:
            session.execute(text("ALTER TABLE rv_hidden RENAME TO rules_version"))
            session.commit()
