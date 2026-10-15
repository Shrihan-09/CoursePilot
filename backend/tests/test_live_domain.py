"""CoursePilot Live (Phase 6.9) - pure rules and the APNs adapter, offline.

The APNs adapter is exercised against an httpx.MockTransport with a signing
key GENERATED FOR THE TEST (never committed). No request reaches Apple and no
iPhone is involved: the tested boundary ends at the HTTP request CoursePilot
would send.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.domain.live import (
    BACKOFF_CAP,
    LIVE_ACTIVITY_DISMISS_AFTER,
    MAX_PAYLOAD_BYTES,
    ErrorClass,
    OpeningFacts,
    Outcome,
    alert_payload,
    classify_apns,
    live_activity_payload,
    next_attempt,
    opening_state,
    token_fingerprint,
    token_hash,
    valid_token,
)
from app.services.push_providers import (
    ApnsConfigError,
    ApnsProvider,
    FakeProvider,
    PushRequest,
    provider_from_settings,
)

T = datetime(2026, 10, 14, 2, 31, 11, tzinfo=UTC)
TOKEN = "a1b2c3d4" * 8                                   # 64 hex characters, SYNTHETIC
LAB = OpeningFacts(event_id=str(uuid.UUID(int=1)), watch_id=str(uuid.UUID(int=2)),
                   term_code="20269", course_string="01:750:194", supplement_code="LB",
                   section_number="L1", index_number="13385", observed_at=T,
                   component_state="closed", component_open=0, component_total=8,
                   component_as_of=T)
PLAIN = OpeningFacts(event_id=str(uuid.UUID(int=3)), watch_id=str(uuid.UUID(int=4)),
                     term_code="20269", course_string="01:750:229", supplement_code="",
                     section_number="01", index_number="13551", observed_at=T)


# ---------------------------------------------------------------- APNs responses


@pytest.mark.parametrize(("status", "reason", "outcome", "error"), [
    (200, None, Outcome.ACCEPTED, None),
    (400, "BadDeviceToken", Outcome.ENDPOINT_INVALID, ErrorClass.ENDPOINT_INVALID),
    (400, "DeviceTokenNotForTopic", Outcome.ENDPOINT_INVALID, ErrorClass.ENDPOINT_INVALID),
    (410, "Unregistered", Outcome.ENDPOINT_INVALID, ErrorClass.ENDPOINT_INVALID),
    (410, "ExpiredToken", Outcome.ENDPOINT_INVALID, ErrorClass.ENDPOINT_INVALID),
    (413, "PayloadTooLarge", Outcome.PERMANENT, ErrorClass.PAYLOAD_TOO_LARGE),
    (400, "BadTopic", Outcome.PERMANENT, ErrorClass.REQUEST_INVALID),
    (403, "Forbidden", Outcome.PERMANENT, ErrorClass.REQUEST_INVALID),
    (403, "ExpiredProviderToken", Outcome.RETRY, ErrorClass.PROVIDER_AUTH),
    (403, "InvalidProviderToken", Outcome.RETRY, ErrorClass.PROVIDER_AUTH),
    (429, "TooManyRequests", Outcome.RETRY, ErrorClass.RATE_LIMITED),
    (500, "InternalServerError", Outcome.RETRY, ErrorClass.PROVIDER_INTERNAL),
    (503, "ServiceUnavailable", Outcome.RETRY, ErrorClass.PROVIDER_UNAVAILABLE),
    (599, None, Outcome.RETRY, ErrorClass.UNKNOWN),
])
def test_apns_responses_follow_apples_table(status, reason, outcome, error) -> None:
    result = classify_apns(status, reason)
    assert (result.outcome, result.error_class) == (outcome, error)


def test_a_provider_auth_error_never_blames_the_device() -> None:
    """Our JWT/key is wrong: the device token stays valid (retry, bounded)."""
    assert classify_apns(403, "ExpiredProviderToken").outcome is not Outcome.ENDPOINT_INVALID


def test_backoff_is_exponential_bounded_and_honours_retry_after() -> None:
    rng = random.Random(7)
    delays = [(next_attempt(n, T, rng=rng) - T) for n in range(1, 12)]
    assert delays[0] >= timedelta(seconds=30) and delays[1] > delays[0]
    assert max(delays) <= BACKOFF_CAP * 1.2                  # cap + at most 20% jitter
    assert next_attempt(1, T, retry_after=timedelta(minutes=10), rng=rng) - T >= timedelta(
        minutes=10)


# ---------------------------------------------------------------- tokens


def test_tokens_are_validated_hashed_and_only_fingerprinted() -> None:
    assert valid_token(TOKEN) and valid_token(TOKEN.upper())
    for bad in ("", "xyz", "12" * 8, TOKEN + " ", "g" * 64):
        assert not valid_token(bad)
    assert token_hash(TOKEN) == token_hash(TOKEN.upper()) and len(token_hash(TOKEN)) == 64
    fp = token_fingerprint(TOKEN)
    assert len(fp) == 12 and fp not in TOKEN and fp != token_hash(TOKEN)[:12]


# ---------------------------------------------------------------- payloads


def test_payloads_are_structured_and_carry_no_english_to_parse() -> None:
    body = json.loads(alert_payload(PLAIN))
    alert = body["aps"]["alert"]
    assert alert["title-loc-key"] == "CP_SECTION_OPENED_TITLE"
    assert alert["loc-args"] == ["01:750:229", "01"]
    state = body["coursepilot"]
    assert {k: state[k] for k in ("kind", "course", "section", "index", "term",
                                  "availability")} == {
        "kind": "section_opened", "course": "01:750:229", "section": "01", "index": "13551",
        "term": "20269", "availability": "open"}
    assert state["observed_at"] == int(T.timestamp())
    assert state["deep_link"].startswith("coursepilot://watches/")
    text = json.dumps(body).lower()
    for claim in ("register", "eligible", "seat", "available now", "you can"):
        assert claim not in text                             # an observation, nothing more


def test_component_context_only_when_the_event_recorded_it() -> None:
    """01:750:229 has no registration component: no lab field, not even an empty one."""
    assert "required_component" not in opening_state(PLAIN)
    assert "supplement" not in opening_state(PLAIN)
    lab = opening_state(LAB)
    assert lab["required_component"] == {"state": "closed", "open": 0, "total": 8,
                                         "as_of": int(T.timestamp())}
    assert lab["supplement"] == "LB"


def test_live_activity_payload_ends_the_activity_with_the_open_state() -> None:
    aps = json.loads(live_activity_payload(LAB))["aps"]
    assert aps["event"] == "end" and aps["timestamp"] == int(T.timestamp())
    assert aps["dismissal-date"] == int((T + LIVE_ACTIVITY_DISMISS_AFTER).timestamp())
    assert aps["content-state"]["availability"] == "open"
    assert aps["content-state"] == opening_state(LAB)        # one schema for both surfaces


def test_payloads_are_deterministic_and_within_apples_limit() -> None:
    for facts in (LAB, PLAIN):
        for render in (alert_payload, live_activity_payload):
            assert render(facts) == render(facts)
            assert len(render(facts)) < MAX_PAYLOAD_BYTES


# ---------------------------------------------------------------- the APNs adapter


def _key_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())             # generated for this test only
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def _request(channel="apns_alert", environment="sandbox") -> PushRequest:
    return PushRequest(delivery_id=uuid.UUID(int=9), channel=channel, environment=environment,
                       payload=alert_payload(PLAIN), expiration=T + timedelta(hours=1),
                       collapse_id=PLAIN.event_id, token=TOKEN)


def _apns(handler, clock=lambda: 1_800_000_000.0) -> ApnsProvider:
    return ApnsProvider(key_pem=_key_pem(), key_id="ABC123DEFG", team_id="TEAM123456",
                        bundle_id="edu.example.coursepilot",
                        transport=httpx.MockTransport(handler), clock=clock)


def test_apns_live_activity_request_matches_apples_contract() -> None:
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    import jwt
    provider = _apns(handler)
    result = provider.send(_request("apns_live_activity", "production"))
    [req] = seen
    assert result.outcome is Outcome.ACCEPTED
    assert str(req.url) == f"https://api.push.apple.com/3/device/{TOKEN}"
    assert req.headers["apns-push-type"] == "liveactivity"
    assert req.headers["apns-topic"] == "edu.example.coursepilot.push-type.liveactivity"
    assert req.headers["apns-priority"] == "10"
    assert req.headers["apns-id"] == str(uuid.UUID(int=9))
    claims = jwt.decode(req.headers["authorization"].split()[1],
                        options={"verify_signature": False})
    assert claims == {"iss": "TEAM123456", "iat": 1_800_000_000}
    assert jwt.get_unverified_header(req.headers["authorization"].split()[1]) == {
        "alg": "ES256", "kid": "ABC123DEFG", "typ": "JWT"}


def test_apns_alert_request_uses_the_bundle_topic_and_collapses() -> None:
    seen = []
    provider = _apns(lambda r: (seen.append(r), httpx.Response(200))[1])
    provider.send(_request())
    assert seen[0].headers["apns-push-type"] == "alert"
    assert seen[0].headers["apns-topic"] == "edu.example.coursepilot"
    assert seen[0].headers["apns-collapse-id"] == PLAIN.event_id
    assert str(seen[0].url).startswith("https://api.sandbox.push.apple.com/")


def test_the_provider_jwt_is_reused_then_refreshed() -> None:
    now = [1_800_000_000.0]
    tokens = []
    provider = _apns(lambda r: (tokens.append(r.headers["authorization"]),
                                httpx.Response(200))[1], clock=lambda: now[0])
    provider.send(_request())
    now[0] += 600
    provider.send(_request())                                # 10 min: same JWT
    now[0] += 3000
    provider.send(_request())                                # > 40 min: a new one
    assert tokens[0] == tokens[1] != tokens[2]


def test_apns_error_bodies_are_classified() -> None:
    provider = _apns(lambda r: httpx.Response(410, json={"reason": "Unregistered",
                                                         "timestamp": 1}))
    assert provider.send(_request()).outcome is Outcome.ENDPOINT_INVALID
    provider = _apns(lambda r: httpx.Response(429, json={"reason": "TooManyRequests"},
                                              headers={"retry-after": "120"}))
    result = provider.send(_request())
    assert result.outcome is Outcome.RETRY and result.retry_after == timedelta(seconds=120)


def test_network_failures_retry_and_never_carry_the_token(caplog) -> None:
    """httpx puts the URL - with the device token - in exception text and in
    its INFO request log. Neither may surface."""
    caplog.set_level(logging.INFO)

    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    result = _apns(boom).send(_request())
    assert result.outcome is Outcome.RETRY and result.error_class is ErrorClass.NETWORK
    assert TOKEN not in repr(result) and TOKEN not in str(result)
    _apns(lambda r: httpx.Response(200)).send(_request())    # the success path logs too
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert TOKEN not in logged


def test_a_push_request_never_reprs_its_token() -> None:
    assert TOKEN not in repr(_request())


def test_provider_selection_is_safe_by_default() -> None:
    from types import SimpleNamespace

    from pydantic import SecretStr
    assert provider_from_settings(SimpleNamespace(push_provider="none")) is None
    assert isinstance(provider_from_settings(SimpleNamespace(push_provider="fake")),
                      FakeProvider)
    with pytest.raises(ApnsConfigError) as err:
        provider_from_settings(SimpleNamespace(
            push_provider="apns", apns_private_key=SecretStr("SECRET-KEY-MATERIAL"),
            apns_key_path=None, apns_key_id=None, apns_team_id="TEAMID", apns_bundle_id=None))
    assert "SECRET-KEY-MATERIAL" not in str(err.value) and "apns_key_id" in str(err.value)
