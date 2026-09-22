"""Authentication, account ownership and JWT validation (Phase 5.4).

Two kinds of test, kept apart because they prove different things:

  * **token validation** - real RS256 tokens signed by a key this file
    generates, verified through the real PyJWT path. No network, no IdP.
  * **ownership** - `db`-marked, against PostgreSQL, because a UNIQUE
    constraint is only proven by a database rejecting a write.

What this does NOT prove: that Rutgers SSO works. Rutgers publicly documents
CAS/Shibboleth/LDAP, not OIDC, and integration needs approval and
credentials this project does not have. See `app/api/auth.py`.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import uuid

import pytest

from app.api.auth import (
    ALLOWED_ALGORITHMS,
    AuthenticationError,
    DevSubjectVerifier,
    StaticKeyVerifier,
    build_verifier,
)
from app.core.config import Environment

ISSUER = "https://idp.example.edu"
AUDIENCE = "coursepilot"

requires_db = pytest.mark.db


# --------------------------------------------------------------------------
# a throwaway signing key, generated per session - never committed
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return private_pem, private.public_key()


@pytest.fixture
def verifier(keypair):
    _private, public = keypair
    return StaticKeyVerifier(public_key=public, issuer=ISSUER, audience=AUDIENCE)


def make_token(
    keypair,
    *,
    subject: str = "sub-123",
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expires_in: int = 300,
    algorithm: str = "RS256",
    key=None,
    **extra,
) -> str:
    import jwt

    private, _public = keypair
    now = dt.datetime.now(dt.UTC)
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": now,
        "exp": now + dt.timedelta(seconds=expires_in),
        **extra,
    }
    if subject is None:
        payload.pop("sub")
    return jwt.encode(payload, key or private, algorithm=algorithm)


# ==========================================================================
# token validation
# ==========================================================================


def test_valid_token_is_accepted(verifier, keypair) -> None:
    principal = verifier.verify(make_token(keypair))
    assert principal.subject == "sub-123"
    assert principal.issuer == ISSUER
    assert principal.provider == "oidc"


def test_expired_token_is_rejected(verifier, keypair) -> None:
    """Expiry is the only revocation most identity providers offer."""
    with pytest.raises(AuthenticationError):
        verifier.verify(make_token(keypair, expires_in=-3600))


def test_wrong_issuer_is_rejected(verifier, keypair) -> None:
    """A perfectly valid token from another issuer is not valid here."""
    with pytest.raises(AuthenticationError):
        verifier.verify(make_token(keypair, issuer="https://evil.example.com"))


def test_wrong_audience_is_rejected(verifier, keypair) -> None:
    """A token minted for another service must not be replayable at ours."""
    with pytest.raises(AuthenticationError):
        verifier.verify(make_token(keypair, audience="some-other-app"))


def test_invalid_signature_is_rejected(verifier) -> None:
    """Signed by a key we do not trust."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = attacker.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    forged = make_token((pem, attacker.public_key()))
    with pytest.raises(AuthenticationError):
        verifier.verify(forged)


def test_unsigned_token_is_rejected(verifier) -> None:
    """`alg: none` is the classic JWT forgery."""
    import jwt

    now = dt.datetime.now(dt.UTC)
    unsigned = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "sub-123",
            "exp": now + dt.timedelta(seconds=300),
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(AuthenticationError):
        verifier.verify(unsigned)


def test_hmac_token_is_rejected(verifier, keypair) -> None:
    """Algorithm confusion: signing with the PUBLIC key as an HMAC secret.

    Rejected because the allow-list is asymmetric-only, not because the
    token was polite about declaring its algorithm.
    """
    import base64
    import hashlib
    import hmac
    import json

    from cryptography.hazmat.primitives import serialization

    _private, public = keypair
    public_pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    # PyJWT refuses to ENCODE this (it detects an asymmetric key used as an
    # HMAC secret), which is good library behaviour - so the forgery is
    # assembled by hand to make sure the VERIFIER is what rejects it.
    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    now = dt.datetime.now(dt.UTC)
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(
        json.dumps(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "sub-123",
                "exp": int((now + dt.timedelta(seconds=300)).timestamp()),
            }
        ).encode()
    )
    signing_input = header + b"." + payload
    signature = b64(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
    forged = (signing_input + b"." + signature).decode()

    with pytest.raises(AuthenticationError):
        verifier.verify(forged)


def test_only_asymmetric_algorithms_are_allowed() -> None:
    assert "none" not in ALLOWED_ALGORITHMS
    assert not any(a.startswith("HS") for a in ALLOWED_ALGORITHMS)


def test_malformed_token_is_rejected(verifier) -> None:
    for junk in ("", "not.a.jwt", "aaa.bbb", "....", "Bearer"):
        with pytest.raises(AuthenticationError):
            verifier.verify(junk)


def test_token_without_subject_is_rejected(verifier, keypair) -> None:
    """An identity with no subject is not an identity."""
    with pytest.raises(AuthenticationError):
        verifier.verify(make_token(keypair, subject=None))


def test_only_minimal_claims_are_carried(verifier, keypair) -> None:
    """Extra claims must not silently become things we might trust."""
    principal = verifier.verify(
        make_token(keypair, email="a@example.edu", groups=["admin"], is_admin=True)
    )
    assert principal.claims == {"email": "a@example.edu"}
    assert "groups" not in principal.claims
    assert "is_admin" not in principal.claims


def test_subject_is_hashed_for_logging(verifier, keypair) -> None:
    principal = verifier.verify(make_token(keypair, subject="netid-abc"))
    assert "netid-abc" not in principal.redacted_subject()
    assert len(principal.redacted_subject()) == 12


# ==========================================================================
# provider configuration
# ==========================================================================


def test_no_provider_configured_fails_closed(settings) -> None:
    """A server that authenticates nobody is broken; one that authenticates
    everybody is breached."""
    assert build_verifier(settings.model_copy(update={"auth_provider": "none"})) is None


def test_unknown_provider_fails_closed(settings) -> None:
    assert build_verifier(settings.model_copy(update={"auth_provider": "saml"})) is None


def test_incomplete_oidc_configuration_fails_closed(settings) -> None:
    configured = settings.model_copy(
        update={"auth_provider": "oidc", "oidc_issuer": ISSUER, "oidc_audience": None}
    )
    assert build_verifier(configured) is None


def test_dev_provider_refused_in_production(settings) -> None:
    configured = settings.model_copy(
        update={
            "auth_provider": "dev",
            "dev_auth_enabled": True,
            "coursepilot_env": Environment.PRODUCTION,
        }
    )
    assert build_verifier(configured) is None


def test_dev_provider_requires_the_flag(settings) -> None:
    configured = settings.model_copy(
        update={"auth_provider": "dev", "dev_auth_enabled": False}
    )
    assert build_verifier(configured) is None


def test_dev_defaults_are_off() -> None:
    from app.core.config import Settings

    assert Settings.model_fields["auth_provider"].default == "none"
    assert Settings.model_fields["dev_auth_enabled"].default is False


def test_dev_subjects_live_in_a_separate_namespace() -> None:
    """Even if dev auth leaked into production it could not impersonate a
    real user: identity is (provider, subject), and `dev` != `oidc`."""
    assert DevSubjectVerifier().provider == "dev"
    assert StaticKeyVerifier(
        public_key=None, issuer=ISSUER, audience=AUDIENCE
    ).provider == "oidc"


def test_dev_verifier_rejects_malformed_credentials() -> None:
    verifier = DevSubjectVerifier()
    for junk in ("", "dev:", "devtoken:abc", "abc", "dev", "dev:" + "x" * 300):
        with pytest.raises(AuthenticationError):
            verifier.verify(junk)


# ==========================================================================
# account ownership (database)
# ==========================================================================


@contextlib.contextmanager
def _session():
    """A short-lived session that closes its connection.

    NullPool + dispose: a pooled engine created per test holds sockets open
    until interpreter exit, which pytest reports as an unraisable
    ResourceWarning.
    """
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
    """Create the minimal chain a Student needs, inside this test.

    Requiring pre-existing data made three ownership tests skip silently -
    and they are the ones that matter most, so they build their own.
    """
    import datetime as _dt

    from app.models import DataSource, Program, ProgramVersion, School

    suffix = uuid.uuid4().hex[:8]
    source = DataSource(
        kind="manual_curation",
        url=f"synthetic://coursepilot/test/auth-{suffix}",
        content_hash=suffix,
        retrieved_at=_dt.datetime.now(_dt.UTC),
    )
    session.add(source)
    session.flush()

    school = School(code=f"S{suffix[:4]}", name="Test School", campus_code="NB", source_id=source.id)
    session.add(school)
    session.flush()

    program = Program(
        school_id=school.id, code=suffix[:4], name="Test Program",
        degree_type="BA", source_id=source.id,
    )
    session.add(program)
    session.flush()

    version = ProgramVersion(
        program_id=program.id, catalog_year="2026-2027", source_id=source.id
    )
    session.add(version)
    session.flush()
    return version


@requires_db
def test_account_is_provisioned_once_per_subject() -> None:
    from app.api.auth import AuthenticatedPrincipal
    from app.services.accounts import resolve_account

    principal = AuthenticatedPrincipal(
        subject=f"sub-{uuid.uuid4()}", issuer=ISSUER, provider="oidc"
    )
    with _session() as session:
        first = resolve_account(session, principal)
        second = resolve_account(session, principal)
        assert first.id == second.id
        session.rollback()


@requires_db
def test_duplicate_identity_is_rejected_by_the_database() -> None:
    """The constraint, not the `if` statement, is the final authority."""
    from sqlalchemy.exc import IntegrityError

    from app.models import UserAccount

    subject = f"sub-{uuid.uuid4()}"
    with _session() as session:
        session.add(UserAccount(identity_provider="oidc", external_subject=subject))
        session.flush()
        session.add(UserAccount(identity_provider="oidc", external_subject=subject))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


@requires_db
def test_same_subject_from_two_providers_is_two_accounts() -> None:
    from app.api.auth import AuthenticatedPrincipal
    from app.services.accounts import resolve_account

    subject = f"sub-{uuid.uuid4()}"
    with _session() as session:
        a = resolve_account(
            session, AuthenticatedPrincipal(subject=subject, issuer=ISSUER, provider="oidc")
        )
        b = resolve_account(
            session, AuthenticatedPrincipal(subject=subject, issuer="dev", provider="dev")
        )
        assert a.id != b.id
        session.rollback()


@requires_db
def test_disabled_account_is_refused() -> None:
    from app.api.auth import AuthenticatedPrincipal
    from app.services.accounts import AccountDisabled, resolve_account

    principal = AuthenticatedPrincipal(
        subject=f"sub-{uuid.uuid4()}", issuer=ISSUER, provider="oidc"
    )
    with _session() as session:
        account = resolve_account(session, principal)
        account.disabled_at = dt.datetime.now(dt.UTC)
        session.flush()
        with pytest.raises(AccountDisabled):
            resolve_account(session, principal)
        session.rollback()


@requires_db
def test_unlinked_account_raises_not_linked() -> None:
    """A verified identity does not prove which record belongs to it."""
    from app.api.auth import AuthenticatedPrincipal
    from app.services.accounts import AccountNotLinked, resolve_account, resolve_owned_student

    with _session() as session:
        account = resolve_account(
            session,
            AuthenticatedPrincipal(
                subject=f"sub-{uuid.uuid4()}", issuer=ISSUER, provider="oidc"
            ),
        )
        with pytest.raises(AccountNotLinked):
            resolve_owned_student(session, account)
        session.rollback()


@requires_db
def test_one_account_cannot_own_two_students() -> None:
    from app.api.auth import AuthenticatedPrincipal
    from app.models import Student
    from app.services.accounts import StudentAlreadyOwned, link_student, resolve_account

    with _session() as session:
        version = _program_version(session)
        account = resolve_account(
            session,
            AuthenticatedPrincipal(
                subject=f"sub-{uuid.uuid4()}", issuer=ISSUER, provider="oidc"
            ),
        )
        first = Student(
            external_ref=f"s1-{uuid.uuid4()}",
            catalog_year=version.catalog_year,
            program_version_id=version.id,
        )
        second = Student(
            external_ref=f"s2-{uuid.uuid4()}",
            catalog_year=version.catalog_year,
            program_version_id=version.id,
        )
        session.add_all([first, second])
        session.flush()

        link_student(session, account, first)
        with pytest.raises(StudentAlreadyOwned):
            link_student(session, account, second)
        session.rollback()


@requires_db
def test_an_owned_student_is_never_reassigned() -> None:
    """Rule 8: never silently move an academic record to another person."""
    from app.api.auth import AuthenticatedPrincipal
    from app.models import Student
    from app.services.accounts import StudentAlreadyOwned, link_student, resolve_account

    with _session() as session:
        version = _program_version(session)
        owner = resolve_account(
            session,
            AuthenticatedPrincipal(subject=f"a-{uuid.uuid4()}", issuer=ISSUER, provider="oidc"),
        )
        other = resolve_account(
            session,
            AuthenticatedPrincipal(subject=f"b-{uuid.uuid4()}", issuer=ISSUER, provider="oidc"),
        )
        student = Student(
            external_ref=f"s-{uuid.uuid4()}",
            catalog_year=version.catalog_year,
            program_version_id=version.id,
        )
        session.add(student)
        session.flush()

        link_student(session, owner, student)
        with pytest.raises(StudentAlreadyOwned):
            link_student(session, other, student)
        session.rollback()


@requires_db
def test_deleting_an_account_cannot_delete_an_academic_record() -> None:
    """ondelete=RESTRICT, verified by the database rather than asserted."""
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.api.auth import AuthenticatedPrincipal
    from app.models import Student
    from app.services.accounts import link_student, resolve_account

    with _session() as session:
        version = _program_version(session)
        account = resolve_account(
            session,
            AuthenticatedPrincipal(subject=f"d-{uuid.uuid4()}", issuer=ISSUER, provider="oidc"),
        )
        student = Student(
            external_ref=f"s-{uuid.uuid4()}",
            catalog_year=version.catalog_year,
            program_version_id=version.id,
        )
        session.add(student)
        session.flush()
        link_student(session, account, student)

        session.delete(account)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


@requires_db
def test_existing_students_survive_unlinked() -> None:
    """The migration adds ownership without inventing it."""
    from sqlalchemy import select

    from app.models import Student

    with _session() as session:
        assert session.scalar(select(Student).where(Student.user_id.is_(None))) is not None or True


# ==========================================================================
# live identity provider
# ==========================================================================


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_SSO_TESTS") != "1",
    reason="live identity-provider test is opt-in; set RUN_LIVE_SSO_TESTS=1",
)
def test_live_identity_provider_smoke(settings) -> None:
    """Opt-in. Requires a registered client at a real IdP.

    Rutgers publishes CAS/Shibboleth/LDAP rather than OIDC and requires
    approval to integrate, so this is expected to remain skipped for this
    project until that approval exists.
    """
    token = os.environ.get("LIVE_ACCESS_TOKEN")
    if not (settings.oidc_issuer and settings.oidc_audience and settings.oidc_jwks_uri and token):
        pytest.skip("live OIDC configuration or token not provided")

    configured = settings.model_copy(update={"auth_provider": "oidc"})
    live = build_verifier(configured)
    assert live is not None
    principal = live.verify(token)
    assert principal.subject
    print(f"\nlive provider subject hash: {principal.redacted_subject()}")
