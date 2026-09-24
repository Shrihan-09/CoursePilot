"""A real local OIDC issuer, served over real HTTP (Phase 5.12).

> This is **not a mock**. It generates real RSA keys, serves a real
> `/.well-known/jwks.json` over a real TCP socket, and mints real RS256
> tokens. `OIDCTokenVerifier` talks to it through `PyJWKClient` exactly as it
> would talk to Rutgers.

## What it proves, and what it cannot

Proves: the JWKS fetch, key selection by `kid`, signature verification,
issuer/audience/expiry checks, key rotation and JWKS-outage behaviour are
correct against a **real HTTP issuer**.

Cannot prove: that Rutgers' specific issuer behaves this way, that their
claims match, or that a client registration exists. Nothing here should be
read as Rutgers verification - see the boundary table in DATA_MODEL.

## Why a real socket rather than a stubbed key

`StaticKeyVerifier` (Phase 5.4) hands the verifier a key object directly, so
it never exercises `PyJWKClient`, the HTTP fetch, the JWKS document format,
`kid` selection or the refresh-on-unknown-kid path. Those are exactly the
parts that break in a real deployment, and exactly the parts a stub hides.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass
class SigningKey:
    kid: str
    private: rsa.RSAPrivateKey

    @property
    def private_pem(self) -> bytes:
        return self.private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def jwk(self) -> dict:
        numbers = self.private.public_key().public_numbers()
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": self.kid,
            "n": _b64(numbers.n),
            "e": _b64(numbers.e),
        }


@dataclass
class LocalOIDCIssuer:
    """A real HTTP JWKS endpoint that can rotate keys and fail on demand."""

    keys: list[SigningKey] = field(default_factory=list)
    #: Fault injection, for the Part 4 failure matrix. All real behaviours:
    #: the verifier sees a genuine HTTP error, not a patched function.
    mode: str = "ok"           # ok | http_500 | malformed | empty_keys
    delay_seconds: float = 0.0
    requests: int = 0

    _server: HTTPServer | None = None
    _thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------

    def start(self) -> LocalOIDCIssuer:
        if not self.keys:
            self.add_key()
        issuer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence the default stderr log
                return

            def do_GET(self):  # noqa: N802
                issuer.requests += 1
                if issuer.delay_seconds:
                    import time

                    time.sleep(issuer.delay_seconds)

                if issuer.mode == "http_500":
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b"upstream failure")
                    return

                if issuer.mode == "malformed":
                    body = b"{ this is not valid json"
                elif issuer.mode == "empty_keys":
                    body = json.dumps({"keys": []}).encode()
                else:
                    body = json.dumps(
                        {"keys": [k.jwk() for k in issuer.keys]}
                    ).encode()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> LocalOIDCIssuer:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- identity ------------------------------------------------------

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.server_address[1]

    @property
    def issuer_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def jwks_uri(self) -> str:
        return f"{self.issuer_url}/.well-known/jwks.json"

    # -- keys ----------------------------------------------------------

    def add_key(self, kid: str | None = None) -> SigningKey:
        """Publish a new signing key, as a real rotation would."""
        key = SigningKey(
            kid=kid or f"kid-{uuid.uuid4().hex[:8]}",
            private=rsa.generate_private_key(public_exponent=65537, key_size=2048),
        )
        self.keys.append(key)
        return key

    def retire_all_but(self, key: SigningKey) -> None:
        self.keys = [key]

    # -- tokens --------------------------------------------------------

    def mint(
        self,
        *,
        key: SigningKey | None = None,
        subject: str = "netid-test",
        audience: str = "coursepilot",
        issuer: str | None = None,
        expires_in: int = 300,
        not_before: int | None = None,
        algorithm: str = "RS256",
        include_kid: bool = True,
        **extra,
    ) -> str:
        """A real RS256 token, signed by a real published key."""
        signer = key or self.keys[0]
        now = dt.datetime.now(dt.UTC)
        payload = {
            "iss": issuer if issuer is not None else self.issuer_url,
            "aud": audience,
            "iat": now,
            "exp": now + dt.timedelta(seconds=expires_in),
            **extra,
        }
        if subject is not None:
            payload["sub"] = subject
        if not_before is not None:
            payload["nbf"] = now + dt.timedelta(seconds=not_before)

        headers = {"kid": signer.kid} if include_kid else {}
        return jwt.encode(payload, signer.private_pem, algorithm=algorithm,
                          headers=headers)


__all__ = ["LocalOIDCIssuer", "SigningKey"]
