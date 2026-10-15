"""Push providers (Phase 6.9): the boundary between the outbox and Apple.

`send(request) -> SendResult` never raises for a provider or network
failure: it CLASSIFIES (app.domain.live.classify_apns), so one failing
endpoint never aborts a batch.

Tokens never leave this module in readable form. The APNs URL path contains
the device token (`/3/device/<token>`), and httpx both LOGS request URLs at
INFO and puts them in exception text. So: the httpx/httpcore/hpack loggers
are held at WARNING, every httpx error is translated into a SendResult
without its message, and nothing here logs a traceback.

Providers:
  * FakeProvider - deterministic, scripted per token fingerprint; tests only.
  * ApnsProvider - HTTP/2 + ES256 JWT (Apple "Establishing a token-based
    connection to APNs"). Needs credentials OUTSIDE the repository; none
    exist in this project yet, so it is exercised only against a mock
    transport (DATA_MODEL 45.12 lists the remaining Apple setup).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

import httpx

from app.domain.live import (
    ErrorClass,
    Outcome,
    SendResult,
    classify_apns,
    token_fingerprint,
)

for _name in ("httpx", "httpcore", "hpack", "h2"):
    logging.getLogger(_name).setLevel(logging.WARNING)

APNS_HOSTS = {"production": "https://api.push.apple.com",
              "sandbox": "https://api.sandbox.push.apple.com"}
JWT_REFRESH = timedelta(minutes=40)          # Apple: no more often than 20, no less than 60


@dataclass(frozen=True)
class PushRequest:
    """No ORM object crosses this boundary. `token` is excluded from repr."""

    delivery_id: uuid.UUID
    channel: str                             # apns_alert | apns_live_activity
    environment: str                         # sandbox | production
    payload: bytes
    expiration: datetime
    collapse_id: str
    token: str = field(repr=False)

    @property
    def fingerprint(self) -> str:
        return token_fingerprint(self.token)


class PushProvider(Protocol):
    name: str

    def send(self, request: PushRequest) -> SendResult: ...


class FakeProvider:
    """Deterministic provider for tests: results scripted per token
    fingerprint (a list is consumed in order), ACCEPTED by default."""

    name = "fake"

    def __init__(self, script: dict[str, list[SendResult]] | None = None,
                 default: SendResult | None = None,
                 on_send: Callable[[PushRequest], None] | None = None) -> None:
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.default = default or SendResult(Outcome.ACCEPTED, provider_status=200)
        self.sent: list[PushRequest] = []
        self.on_send = on_send
        self._lock = threading.Lock()

    def send(self, request: PushRequest) -> SendResult:
        with self._lock:
            self.sent.append(request)
            queue = self.script.get(request.fingerprint)
            result = queue.pop(0) if queue else self.default
        if self.on_send:
            self.on_send(request)
        return result


class ApnsConfigError(RuntimeError):
    """APNs is selected but not configured. Message names settings, never values."""


class ApnsProvider:
    name = "apns"

    def __init__(self, *, key_pem: str, key_id: str, team_id: str, bundle_id: str,
                 timeout: float = 30.0, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        missing = [n for n, v in (("apns_private_key/apns_key_path", key_pem),
                                  ("apns_key_id", key_id), ("apns_team_id", team_id),
                                  ("apns_bundle_id", bundle_id)) if not v]
        if missing:
            raise ApnsConfigError(f"APNs provider selected but not configured: {missing}")
        self._key = key_pem
        self._key_id, self._team_id, self.bundle_id = key_id, team_id, bundle_id
        self._clock = clock
        self._jwt: tuple[float, str] | None = None
        if transport is None:                # HTTP/2 is required by APNs (httpx[http2])
            transport = httpx.HTTPTransport(http2=True)
        self._client = httpx.Client(transport=transport, timeout=timeout)

    def __repr__(self) -> str:               # never the key
        return f"ApnsProvider(bundle_id={self.bundle_id!r})"

    def _bearer(self) -> str:
        import jwt  # pyjwt[crypto]; ES256 per Apple

        now = self._clock()
        if self._jwt is None or now - self._jwt[0] >= JWT_REFRESH.total_seconds():
            token = jwt.encode({"iss": self._team_id, "iat": int(now)}, self._key,
                               algorithm="ES256", headers={"kid": self._key_id})
            self._jwt = (now, token)
        return self._jwt[1]

    def headers(self, request: PushRequest) -> dict[str, str]:
        live = request.channel == "apns_live_activity"
        out = {
            "authorization": f"bearer {self._bearer()}",
            "apns-push-type": "liveactivity" if live else "alert",
            "apns-topic": f"{self.bundle_id}.push-type.liveactivity" if live else self.bundle_id,
            "apns-priority": "10",            # an opening is time-sensitive and alerts
            "apns-expiration": str(int(request.expiration.timestamp())),
            "apns-id": str(request.delivery_id),
        }
        if not live:
            out["apns-collapse-id"] = request.collapse_id     # a re-send shows once
        return out

    def send(self, request: PushRequest) -> SendResult:
        url = f"{APNS_HOSTS[request.environment]}/3/device/{request.token}"
        try:
            response = self._client.post(url, content=request.payload,
                                         headers=self.headers(request))
        except httpx.TimeoutException:
            return SendResult(Outcome.RETRY, ErrorClass.TIMEOUT)
        except httpx.HTTPError:
            # The exception text contains the URL - and so the token. Dropped.
            return SendResult(Outcome.RETRY, ErrorClass.NETWORK)
        reason = None
        if response.status_code != 200:
            try:
                reason = str(response.json().get("reason") or "")[:64] or None
            except ValueError:
                reason = None
        retry_after = None
        if "retry-after" in response.headers:
            try:
                retry_after = timedelta(seconds=int(response.headers["retry-after"]))
            except ValueError:
                retry_after = None
        return classify_apns(response.status_code, reason, retry_after)


def provider_from_settings(settings) -> PushProvider | None:
    """None when push is not configured (`push_provider=none`, the default):
    deliveries then stay pending and visible, nothing is sent anywhere."""
    choice = (settings.push_provider or "none").lower()
    if choice == "none":
        return None
    if choice == "fake":
        return FakeProvider()
    if choice == "apns":
        key = settings.apns_private_key.get_secret_value() if settings.apns_private_key else None
        if not key and settings.apns_key_path:
            with open(settings.apns_key_path, encoding="utf-8") as fh:
                key = fh.read()
        return ApnsProvider(key_pem=key or "", key_id=settings.apns_key_id or "",
                            team_id=settings.apns_team_id or "",
                            bundle_id=settings.apns_bundle_id or "")
    raise ApnsConfigError(f"unknown push_provider {choice!r}")


__all__ = ["APNS_HOSTS", "ApnsConfigError", "ApnsProvider", "FakeProvider", "PushProvider",
           "PushRequest", "provider_from_settings"]
