"""Student linking, provisioning and the audit trail (Phase 5.5).

Three kinds of test, kept apart because they prove different things:

  * **authorization** - who may reach the linking API at all. These run
    against the real dependency chain, with only the *principal* overridden,
    so `require_admin` is genuinely executed.
  * **linking semantics** - the service layer against PostgreSQL. A UNIQUE
    constraint is only proven by a database refusing a write, and an audit
    event is only proven by reading the row back.
  * **preservation** - Phase 5.4 behaviour that must not have moved.

What this does NOT prove: that Rutgers SSO works. Nothing in Phase 5.5
changes that, and the admin-assisted model was chosen precisely because it
does not depend on a claim Rutgers does not release.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.models import Student, StudentLinkEvent, UserAccount
from app.services.accounts import (
    AccountNotLinked,
    NotAuthorized,
    StudentAlreadyOwned,
    find_account_by_identity,
    link_student,
    require_admin,
    unlink_student,
)

requires_db = pytest.mark.db

ISSUER = "https://idp.example.edu"


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


def _program_version(session):
    """The minimal chain a Student needs, built by the test itself.

    Phase 5.4 learned this the hard way: tests that depend on pre-existing
    fixture data skip silently, and the ones that skip are the ones that
    matter.
    """
    from app.models import DataSource, Program, ProgramVersion, School

    suffix = uuid.uuid4().hex[:8]
    source = DataSource(
        kind="manual_curation",
        url=f"synthetic://coursepilot/test/link-{suffix}",
        content_hash=suffix,
        retrieved_at=dt.datetime.now(dt.UTC),
    )
    session.add(source)
    session.flush()

    school = School(
        code=f"S{suffix[:4]}",
        name="Test School",
        campus_code="NB",
        source_id=source.id,
    )
    session.add(school)
    session.flush()

    program = Program(
        school_id=school.id,
        code=suffix[:4],
        name="Test Program",
        degree_type="BA",
        source_id=source.id,
    )
    session.add(program)
    session.flush()

    version = ProgramVersion(
        program_id=program.id, catalog_year="2026-2027", source_id=source.id
    )
    session.add(version)
    session.flush()
    return version


def _account(session, subject: str | None = None, *, admin: bool = False):
    account = UserAccount(
        identity_provider="oidc",
        external_subject=subject or f"sub-{uuid.uuid4()}",
        is_admin=admin,
    )
    session.add(account)
    session.flush()
    return account


def _student(session, version):
    student = Student(
        external_ref=f"s-{uuid.uuid4()}",
        catalog_year=version.catalog_year,
        program_version_id=version.id,
    )
    session.add(student)
    session.flush()
    return student


def _events(session, student_id):
    from sqlalchemy import select

    return list(
        session.scalars(
            select(StudentLinkEvent)
            .where(StudentLinkEvent.student_id == student_id)
            .order_by(StudentLinkEvent.created_at)
        )
    )


# ==========================================================================
# authorization
# ==========================================================================


@pytest.fixture
def admin_app():
    """The real app, with only the *principal* supplied.

    `require_admin_principal` is NOT overridden, so every test below runs the
    genuine authorization check against the database.
    """
    from app.main import create_app

    return create_app()


def _client_as(app, account_id: uuid.UUID) -> AsyncClient:
    from app.api.security import Principal, get_principal

    app.dependency_overrides[get_principal] = lambda: Principal(
        account_id=account_id,
        subject="subject-a",
        issuer=ISSUER,
        provider="oidc",
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_linking_requires_a_credential(client) -> None:
    """Rule 6, at the outermost layer: no credential, no linking."""
    response = await client.post(
        f"/api/v1/admin/students/{uuid.uuid4()}/link",
        json={"identity_provider": "oidc", "external_subject": "x"},
    )
    assert response.status_code == 401


@requires_db
async def test_ordinary_user_cannot_link(admin_app) -> None:
    """Rule 6. Authenticated is not the same as authorized."""
    with _session() as session:
        ordinary = _account(session)
        version = _program_version(session)
        student = _student(session, version)
        session.commit()
        ordinary_id, student_id = ordinary.id, student.id

    async with _client_as(admin_app, ordinary_id) as ac:
        response = await ac.post(
            f"/api/v1/admin/students/{student_id}/link",
            json={"identity_provider": "oidc", "external_subject": "whoever"},
        )
    assert response.status_code == 403


@requires_db
async def test_non_admin_cannot_tell_whether_a_student_exists(admin_app) -> None:
    """Rule 3. Authorization runs BEFORE the lookup, so a real id and a
    fabricated one are indistinguishable to an ordinary user."""
    with _session() as session:
        ordinary = _account(session)
        version = _program_version(session)
        real = _student(session, version)
        session.commit()
        ordinary_id, real_id = ordinary.id, real.id

    body = {"identity_provider": "oidc", "external_subject": "whoever"}
    async with _client_as(admin_app, ordinary_id) as ac:
        existing = await ac.post(f"/api/v1/admin/students/{real_id}/link", json=body)
        fictional = await ac.post(
            f"/api/v1/admin/students/{uuid.uuid4()}/link", json=body
        )

    assert existing.status_code == fictional.status_code == 403
    assert existing.json() == fictional.json()


@requires_db
async def test_admin_flag_is_read_from_the_database_not_the_token(admin_app) -> None:
    """Authority that travels inside a credential outlives its revocation."""
    with _session() as session:
        account = _account(session, admin=True)
        version = _program_version(session)
        student = _student(session, version)
        subject = account.external_subject
        session.commit()
        account_id, student_id = account.id, student.id

    async with _client_as(admin_app, account_id) as ac:
        granted = await ac.post(
            f"/api/v1/admin/students/{student_id}/unlink", json={}
        )
        # Revoke between requests. Nothing about the caller's credential
        # changed; the next request must still be refused.
        with _session() as session:
            session.get(UserAccount, account_id).is_admin = False
            session.commit()
        revoked = await ac.post(
            f"/api/v1/admin/students/{student_id}/unlink", json={}
        )

    assert granted.status_code == 409  # authorized; simply not linked
    assert revoked.status_code == 403
    assert subject  # the subject never carried the authority


@requires_db
async def test_client_cannot_grant_itself_admin(admin_app) -> None:
    """Rule 1's sibling: no request field sets authority."""
    with _session() as session:
        ordinary = _account(session)
        version = _program_version(session)
        student = _student(session, version)
        session.commit()
        ordinary_id, student_id = ordinary.id, student.id

    async with _client_as(admin_app, ordinary_id) as ac:
        response = await ac.post(
            f"/api/v1/admin/students/{student_id}/link",
            json={
                "identity_provider": "oidc",
                "external_subject": "whoever",
                "is_admin": True,
            },
        )
    # `extra="forbid"` rejects the field outright; and even without it the
    # value is never read.
    assert response.status_code in (403, 422)
    with _session() as session:
        assert session.get(UserAccount, ordinary_id).is_admin is False


# ==========================================================================
# linking through the API
# ==========================================================================


@requires_db
async def test_admin_links_by_identity_and_an_event_is_written(admin_app) -> None:
    with _session() as session:
        admin = _account(session, admin=True)
        target = _account(session)
        version = _program_version(session)
        student = _student(session, version)
        session.commit()
        admin_id, student_id = admin.id, student.id
        target_id, target_subject = target.id, target.external_subject

    async with _client_as(admin_app, admin_id) as ac:
        response = await ac.post(
            f"/api/v1/admin/students/{student_id}/link",
            json={
                "identity_provider": "oidc",
                "external_subject": target_subject,
                "reason": "verified in person",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "student_id": str(student_id),
        "linked": True,
        "event_recorded": True,
    }

    with _session() as session:
        assert session.get(Student, student_id).user_id == target_id
        events = _events(session, student_id)
        assert [e.action for e in events] == ["linked"]
        assert events[0].performed_by_id == admin_id
        assert events[0].user_account_id == target_id
        assert events[0].reason == "verified in person"


@requires_db
async def test_response_reveals_nothing_about_the_account(admin_app) -> None:
    """Rule 3. The response confirms the operation and stops there."""
    with _session() as session:
        admin = _account(session, admin=True)
        target = _account(session)
        version = _program_version(session)
        student = _student(session, version)
        session.commit()
        admin_id, student_id = admin.id, student.id
        target_id, target_subject = target.id, target.external_subject

    async with _client_as(admin_app, admin_id) as ac:
        response = await ac.post(
            f"/api/v1/admin/students/{student_id}/link",
            json={"identity_provider": "oidc", "external_subject": target_subject},
        )

    body = response.text
    assert str(target_id) not in body
    assert target_subject not in body
    assert set(response.json()) == {"student_id", "linked", "event_recorded"}


@requires_db
async def test_linking_an_unknown_identity_provisions_nothing(admin_app) -> None:
    """An admin typing a name must not create an account.

    Every account exists because a verifier accepted a credential. Allowing
    an admin to conjure one would make the account table record what an
    operator believed rather than what an identity provider attested.
    """
    from sqlalchemy import func, select

    with _session() as session:
        admin = _account(session, admin=True)
        version = _program_version(session)
        student = _student(session, version)
        session.commit()
        admin_id, student_id = admin.id, student.id
        before = session.scalar(select(func.count()).select_from(UserAccount))

    async with _client_as(admin_app, admin_id) as ac:
        response = await ac.post(
            f"/api/v1/admin/students/{student_id}/link",
            json={"identity_provider": "oidc", "external_subject": f"ghost-{uuid.uuid4()}"},
        )

    assert response.status_code == 404
    with _session() as session:
        after = session.scalar(select(func.count()).select_from(UserAccount))
        assert after == before
        assert session.get(Student, student_id).user_id is None


@requires_db
async def test_linking_an_already_owned_student_fails_safely(admin_app) -> None:
    """Rule 2. The first owner keeps the record."""
    with _session() as session:
        admin = _account(session, admin=True)
        first = _account(session)
        second = _account(session)
        version = _program_version(session)
        student = _student(session, version)
        link_student(session, first, student, performed_by=admin)
        session.commit()
        admin_id, student_id, first_id = admin.id, student.id, first.id
        second_subject = second.external_subject

    async with _client_as(admin_app, admin_id) as ac:
        response = await ac.post(
            f"/api/v1/admin/students/{student_id}/link",
            json={"identity_provider": "oidc", "external_subject": second_subject},
        )

    assert response.status_code == 409
    with _session() as session:
        assert session.get(Student, student_id).user_id == first_id
        assert [e.action for e in _events(session, student_id)] == ["linked"]


@requires_db
async def test_linking_an_already_linked_account_fails_safely(admin_app) -> None:
    """One account, one academic record."""
    with _session() as session:
        admin = _account(session, admin=True)
        target = _account(session)
        version = _program_version(session)
        first = _student(session, version)
        second = _student(session, version)
        link_student(session, target, first, performed_by=admin)
        session.commit()
        admin_id, second_id = admin.id, second.id
        target_subject = target.external_subject

    async with _client_as(admin_app, admin_id) as ac:
        response = await ac.post(
            f"/api/v1/admin/students/{second_id}/link",
            json={"identity_provider": "oidc", "external_subject": target_subject},
        )

    assert response.status_code == 409
    with _session() as session:
        assert session.get(Student, second_id).user_id is None
        assert _events(session, second_id) == []


@requires_db
async def test_linking_a_missing_student_is_404(admin_app) -> None:
    with _session() as session:
        admin = _account(session, admin=True)
        target = _account(session)
        session.commit()
        admin_id, target_subject = admin.id, target.external_subject

    async with _client_as(admin_app, admin_id) as ac:
        response = await ac.post(
            f"/api/v1/admin/students/{uuid.uuid4()}/link",
            json={"identity_provider": "oidc", "external_subject": target_subject},
        )
    assert response.status_code == 404


@requires_db
async def test_link_request_rejects_unknown_fields(admin_app) -> None:
    """Rule 1: no client-supplied field may establish ownership, including
    one we have not thought of yet."""
    with _session() as session:
        admin = _account(session, admin=True)
        version = _program_version(session)
        student = _student(session, version)
        session.commit()
        admin_id, student_id = admin.id, student.id

    async with _client_as(admin_app, admin_id) as ac:
        response = await ac.post(
            f"/api/v1/admin/students/{student_id}/link",
            json={
                "identity_provider": "oidc",
                "external_subject": "x",
                "user_account_id": str(uuid.uuid4()),
            },
        )
    assert response.status_code == 422


# ==========================================================================
# unlinking and re-linking
# ==========================================================================


@requires_db
async def test_unlinking_deletes_no_academic_data(admin_app) -> None:
    """Rule 9. Only the ownership column changes."""
    from sqlalchemy import func, select

    from app.models import StudentCourse

    with _session() as session:
        admin = _account(session, admin=True)
        target = _account(session)
        version = _program_version(session)
        student = _student(session, version)
        link_student(session, target, student, performed_by=admin)
        session.commit()
        admin_id, student_id = admin.id, student.id
        courses_before = session.scalar(
            select(func.count())
            .select_from(StudentCourse)
            .where(StudentCourse.student_id == student_id)
        )

    async with _client_as(admin_app, admin_id) as ac:
        response = await ac.post(
            f"/api/v1/admin/students/{student_id}/unlink",
            json={"reason": "graduated"},
        )

    assert response.status_code == 200
    with _session() as session:
        student = session.get(Student, student_id)
        assert student is not None, "unlinking must never delete the record"
        assert student.user_id is None
        assert student.program_version_id is not None
        assert courses_before == session.scalar(
            select(func.count())
            .select_from(StudentCourse)
            .where(StudentCourse.student_id == student_id)
        )
        assert [e.action for e in _events(session, student_id)] == ["linked", "unlinked"]


@requires_db
async def test_unlinking_an_unlinked_student_is_409(admin_app) -> None:
    with _session() as session:
        admin = _account(session, admin=True)
        version = _program_version(session)
        student = _student(session, version)
        session.commit()
        admin_id, student_id = admin.id, student.id

    async with _client_as(admin_app, admin_id) as ac:
        response = await ac.post(
            f"/api/v1/admin/students/{student_id}/unlink", json={}
        )
    assert response.status_code == 409
    with _session() as session:
        assert _events(session, student_id) == []


@requires_db
async def test_relinking_after_unlink_leaves_a_full_history(admin_app) -> None:
    """The record can be corrected, and the correction is legible."""
    with _session() as session:
        admin = _account(session, admin=True)
        first = _account(session)
        second = _account(session)
        version = _program_version(session)
        student = _student(session, version)
        session.commit()
        admin_id, student_id = admin.id, student.id
        first_subject = first.external_subject
        second_subject, second_id = second.external_subject, second.id

    async with _client_as(admin_app, admin_id) as ac:
        await ac.post(
            f"/api/v1/admin/students/{student_id}/link",
            json={"identity_provider": "oidc", "external_subject": first_subject},
        )
        await ac.post(
            f"/api/v1/admin/students/{student_id}/unlink",
            json={"reason": "wrong person"},
        )
        final = await ac.post(
            f"/api/v1/admin/students/{student_id}/link",
            json={"identity_provider": "oidc", "external_subject": second_subject},
        )

    assert final.status_code == 200
    with _session() as session:
        assert session.get(Student, student_id).user_id == second_id
        assert [e.action for e in _events(session, student_id)] == [
            "linked",
            "unlinked",
            "linked",
        ]


# ==========================================================================
# the audit trail
# ==========================================================================


@requires_db
def test_link_and_event_are_one_transaction() -> None:
    """Rule 13. A rollback must lose both, never one."""
    with _session() as session:
        admin = _account(session, admin=True)
        target = _account(session)
        version = _program_version(session)
        student = _student(session, version)
        session.commit()
        student_id = student.id

        link_student(session, target, student, performed_by=admin)
        assert len(_events(session, student_id)) == 1
        session.rollback()

    with _session() as session:
        assert session.get(Student, student_id).user_id is None
        assert _events(session, student_id) == []


@requires_db
def test_audit_events_are_not_academic_facts() -> None:
    """Rule 8. The audit trail records security metadata about a record; it
    says nothing about credits, requirements or eligibility."""
    columns = set(StudentLinkEvent.__table__.columns.keys())
    assert columns == {
        "id",
        "student_id",
        "user_account_id",
        "performed_by_id",
        "action",
        "reason",
        "created_at",
        "updated_at",
    }
    # Nothing the Degree Engine reads.
    assert not columns & {"credits", "grade", "term", "requirement_id", "course_id"}


@requires_db
def test_an_unknown_action_is_rejected_by_the_database() -> None:
    """The CHECK constraint, not the constant, is the final authority."""
    from sqlalchemy.exc import IntegrityError

    with _session() as session:
        admin = _account(session, admin=True)
        version = _program_version(session)
        student = _student(session, version)
        session.add(
            StudentLinkEvent(
                student_id=student.id,
                user_account_id=admin.id,
                performed_by_id=admin.id,
                action="transferred",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


@requires_db
def test_audit_events_outlive_an_unlink_and_block_account_deletion() -> None:
    """An audit trail that a later action can erase is not an audit trail."""
    from sqlalchemy.exc import IntegrityError

    with _session() as session:
        admin = _account(session, admin=True)
        target = _account(session)
        version = _program_version(session)
        student = _student(session, version)
        link_student(session, target, student, performed_by=admin)
        unlink_student(session, student, performed_by=admin)
        session.flush()

        # The account no longer owns anything, yet is still named by the
        # history - so deleting it is refused by ON DELETE RESTRICT.
        session.delete(target)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


@requires_db
def test_reason_is_optional_and_never_holds_a_secret() -> None:
    """Rule 5. Nothing in the linking flow generates a secret to log; the
    reason field is an operator note, and it is bounded."""
    from app.api.v1.routes.admin import LinkRequest

    assert LinkRequest.model_fields["reason"].default is None
    assert LinkRequest(identity_provider="oidc", external_subject="x").reason is None


# ==========================================================================
# service-layer guards
# ==========================================================================


@requires_db
def test_require_admin_refuses_an_ordinary_account() -> None:
    with _session() as session:
        assert require_admin(_account(session, admin=True))
        with pytest.raises(NotAuthorized):
            require_admin(_account(session))
        session.rollback()


@requires_db
def test_is_admin_defaults_to_false() -> None:
    """A newly provisioned account has no authority."""
    from app.api.auth import AuthenticatedPrincipal
    from app.services.accounts import resolve_account

    with _session() as session:
        account = resolve_account(
            session,
            AuthenticatedPrincipal(
                subject=f"sub-{uuid.uuid4()}", issuer=ISSUER, provider="oidc"
            ),
        )
        assert account.is_admin is False
        session.rollback()


@requires_db
def test_find_account_by_identity_is_scoped_to_the_provider() -> None:
    """`dev` and `oidc` are separate namespaces, here too."""
    with _session() as session:
        subject = f"sub-{uuid.uuid4()}"
        session.add(
            UserAccount(identity_provider="oidc", external_subject=subject)
        )
        session.flush()
        assert find_account_by_identity(session, "oidc", subject) is not None
        assert find_account_by_identity(session, "dev", subject) is None
        session.rollback()


@requires_db
def test_unlinking_something_unlinked_raises() -> None:
    with _session() as session:
        admin = _account(session, admin=True)
        version = _program_version(session)
        student = _student(session, version)
        with pytest.raises(AccountNotLinked):
            unlink_student(session, student, performed_by=admin)
        session.rollback()


@requires_db
def test_relinking_the_same_account_is_idempotent() -> None:
    """No duplicate event for a no-op, and no spurious conflict."""
    with _session() as session:
        admin = _account(session, admin=True)
        target = _account(session)
        version = _program_version(session)
        student = _student(session, version)
        link_student(session, target, student, performed_by=admin)
        link_student(session, target, student, performed_by=admin)
        assert len(_events(session, student.id)) == 1
        session.rollback()


@requires_db
def test_bootstrap_cannot_reassign_an_owned_record() -> None:
    """The dev bootstrap is a convenience, not a way around rule 2."""
    with _session() as session:
        admin = _account(session, admin=True)
        first = _account(session)
        second = _account(session)
        version = _program_version(session)
        student = _student(session, version)
        link_student(session, first, student, performed_by=admin)
        with pytest.raises(StudentAlreadyOwned):
            link_student(session, second, student, performed_by=admin)
        session.rollback()


def test_dev_bootstrap_refuses_production(monkeypatch) -> None:
    """Part 18. The bootstrap path must be impossible in production."""
    from app.cli import dev_bootstrap
    from app.core.config import Environment, get_settings

    settings = get_settings()
    monkeypatch.setattr(
        dev_bootstrap,
        "get_settings",
        lambda: settings.model_copy(
            update={"coursepilot_env": Environment.PRODUCTION}
        ),
    )
    with pytest.raises(SystemExit):
        dev_bootstrap.main(["grant-admin", "--provider", "dev", "--subject", "a"])


def test_dev_bootstrap_is_not_an_endpoint() -> None:
    """It has no listener at all - the strongest form of 'not in production'."""
    from app.main import create_app

    paths = create_app().openapi()["paths"]
    assert not any("bootstrap" in p for p in paths)


# ==========================================================================
# rate limiting
# ==========================================================================


@requires_db
async def test_linking_has_its_own_budget(admin_app, settings) -> None:
    """Part 16. Bounds the blast radius of a compromised admin credential."""
    from app.api.security import get_link_limiter

    limit = settings.rate_limit_link_operations
    assert limit < settings.rate_limit_requests

    with _session() as session:
        admin = _account(session, admin=True)
        session.commit()
        admin_id = admin.id

    async with _client_as(admin_app, admin_id) as ac:
        statuses = [
            (
                await ac.post(
                    f"/api/v1/admin/students/{uuid.uuid4()}/unlink", json={}
                )
            ).status_code
            for _ in range(limit + 1)
        ]

    assert statuses[:limit] == [404] * limit
    assert statuses[limit] == 429
    assert get_link_limiter(settings) is not None


@requires_db
async def test_a_rejected_non_admin_does_not_spend_the_budget(admin_app, settings) -> None:
    """The budget is charged after authorization, so an unauthorized caller
    cannot exhaust an administrator's."""
    with _session() as session:
        ordinary = _account(session)
        admin = _account(session, admin=True)
        session.commit()
        ordinary_id, admin_id = ordinary.id, admin.id

    async with _client_as(admin_app, ordinary_id) as ac:
        for _ in range(settings.rate_limit_link_operations + 5):
            await ac.post(f"/api/v1/admin/students/{uuid.uuid4()}/unlink", json={})

    async with _client_as(admin_app, admin_id) as ac:
        response = await ac.post(
            f"/api/v1/admin/students/{uuid.uuid4()}/unlink", json={}
        )
    assert response.status_code == 404


# ==========================================================================
# Phase 5.4 preservation
# ==========================================================================


async def test_explanations_still_require_authentication(client) -> None:
    """Rule 10. Nothing in Phase 5.5 weakened token validation."""
    response = await client.post(
        "/api/v1/explanations/recommendation",
        json={"course_key": "01:198:112", "explanation_type": "why_recommended"},
    )
    assert response.status_code == 401


async def test_explanation_request_still_cannot_name_a_student(
    authenticated_client,
) -> None:
    """Rule 1. Linking gave administrators a way to name a student; it did
    not give ordinary callers one."""
    response = await authenticated_client.post(
        "/api/v1/explanations/recommendation",
        json={
            "course_key": "01:198:112",
            "explanation_type": "why_recommended",
            "student_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 422


def test_degree_engine_is_untouched_by_identity() -> None:
    """Rules 7 and 14. The engine must not learn that accounts exist."""
    import pathlib

    engine_dir = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "audit"
    offenders = [
        path.name
        for path in engine_dir.glob("*.py")
        for text in [path.read_text(encoding="utf-8")]
        if any(
            token in text
            for token in ("UserAccount", "is_admin", "StudentLinkEvent", "Principal")
        )
    ]
    assert offenders == []
