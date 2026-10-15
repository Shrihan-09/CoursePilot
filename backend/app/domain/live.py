"""CoursePilot Live (Phase 6.9) - pure rules for notifications and delivery. No I/O.

```
section_availability_change --6.8--> notification_event (immutable fact, per user)
                                          |
                          notification (inbox item: read state lives HERE)
                                          |  fan-out
              notification_delivery (event x endpoint x channel: the outbox)
                                          |  claim -> re-check -> send -> record
                         provider: fake | apns (alert push, Live Activity update)
```

A CoursePilot opening notification reports an OBSERVED Rutgers availability
change. It does not guarantee eligibility, schedule compatibility, or
successful registration. Every payload here is rendered from the event's
structured fields only - never from AI output, never with inferred labs:
component context appears only when the deterministic component model
(Phase 6.6.1) recorded it on the event.

Apple facts used here (developer.apple.com, verified 2026-10-14; see
DATA_MODEL section 45): APNs payload <= 4 KB; Live Activity pushes use
`apns-push-type: liveactivity` and topic `<bundle>.push-type.liveactivity`;
`aps.timestamp` orders updates; `event: end` + `dismissal-date` ends one; a
Live Activity is active at most 8 hours. Error handling follows Apple's
"Handling notification responses from APNs" table.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

#: CoursePilot settings, not Apple or Rutgers policy.
PAYLOAD_VERSION = 1
MAX_PAYLOAD_BYTES = 4096                     # Apple: APNs / ActivityKit 4 KB
EVENT_TTL = timedelta(minutes=60)            # an "opened" alert later than this misleads
MAX_ATTEMPTS = 5
BACKOFF_BASE = timedelta(seconds=30)
BACKOFF_CAP = timedelta(minutes=15)
LEASE = timedelta(seconds=90)                # > provider timeout (30 s)
LIVE_ACTIVITY_MAX = timedelta(hours=8)       # Apple: active at most 8 hours
LIVE_ACTIVITY_DISMISS_AFTER = timedelta(minutes=30)   # Apple HIG: 15-30 minutes
MAX_ENDPOINTS_PER_ACCOUNT = 20
MAX_ACTIVE_LIVE_ACTIVITIES = 10
#: APNs device and ActivityKit tokens are hex; Apple says not to assume a size.
TOKEN_RE = re.compile(r"^[0-9a-fA-F]{32,512}$")
_FINGERPRINT_SALT = "coursepilot-push|"


class EndpointKind(StrEnum):
    APNS_DEVICE = "apns_device"                  # alert pushes (registerForRemoteNotifications)
    APNS_LIVE_ACTIVITY = "apns_live_activity"    # one activity's update token (pushTokenUpdates)
    APNS_PUSH_TO_START = "apns_push_to_start"    # stored only: remote start NOT used (45.7)
    FCM = "fcm"                                  # reserved
    WEB_PUSH = "web_push"                        # reserved


class Channel(StrEnum):
    APNS_ALERT = "apns_alert"
    APNS_LIVE_ACTIVITY = "apns_live_activity"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RETRY = "retry"
    DELIVERED = "delivered"            # APNs accepted it - not proof a person saw it
    FAILED_PERMANENT = "failed_permanent"
    SUPPRESSED = "suppressed"


class SuppressedReason(StrEnum):
    WATCH_INACTIVE = "watch_inactive"
    ENDPOINT_REVOKED = "endpoint_revoked"
    ACTIVITY_ENDED = "activity_ended"
    EVENT_EXPIRED = "event_expired"
    ACCOUNT_DISABLED = "account_disabled"
    #: Apple HIG: don't alert twice for one update. The Live Activity update
    #: carried the alert, so the ordinary push for the same event is not sent.
    LIVE_ACTIVITY_ALERTED = "live_activity_alerted"


class Outcome(StrEnum):
    ACCEPTED = "accepted"
    RETRY = "retry"
    PERMANENT = "permanent"
    ENDPOINT_INVALID = "endpoint_invalid"     # revoke the endpoint; never retry


class ErrorClass(StrEnum):
    ENDPOINT_INVALID = "endpoint_invalid"
    REQUEST_INVALID = "request_invalid"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    PROVIDER_AUTH = "provider_auth"
    RATE_LIMITED = "rate_limited"
    PROVIDER_INTERNAL = "provider_internal"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    NETWORK = "network"
    TIMEOUT = "timeout"
    INTERNAL = "internal"
    UNKNOWN = "unknown"
    MAX_ATTEMPTS = "max_attempts"


@dataclass(frozen=True)
class SendResult:
    outcome: Outcome
    error_class: ErrorClass | None = None
    provider_status: int | None = None
    provider_reason: str | None = None        # Apple's reason token only, never a free-form body
    retry_after: timedelta | None = None


# --------------------------------------------------------------------------
# tokens
# --------------------------------------------------------------------------


def valid_token(token: str) -> bool:
    return bool(TOKEN_RE.match(token or ""))


def token_hash(token: str) -> str:
    """Identity of a token for uniqueness and lookup (never reversible)."""
    return hashlib.sha256(token.lower().encode()).hexdigest()


def token_fingerprint(token: str) -> str:
    """The ONLY form of a token that may appear in a log, a response or an error."""
    return hashlib.sha256((_FINGERPRINT_SALT + token.lower()).encode()).hexdigest()[:12]


# --------------------------------------------------------------------------
# APNs responses -> outcomes (Apple's response table)
# --------------------------------------------------------------------------

_TOKEN_DEAD = {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered", "ExpiredToken"}
_AUTH = {"ExpiredProviderToken", "InvalidProviderToken", "MissingProviderToken",
         "UnrelatedKeyIdInToken", "BadEnvironmentKeyIdInToken"}


def classify_apns(status: int, reason: str | None,
                  retry_after: timedelta | None = None) -> SendResult:
    reason = reason or None
    if status == 200:
        return SendResult(Outcome.ACCEPTED, provider_status=200)
    if status == 410 or (status == 400 and reason in _TOKEN_DEAD):
        return SendResult(Outcome.ENDPOINT_INVALID, ErrorClass.ENDPOINT_INVALID, status, reason)
    if status == 413:
        return SendResult(Outcome.PERMANENT, ErrorClass.PAYLOAD_TOO_LARGE, status, reason)
    if status == 403 and reason in _AUTH:
        # OUR signing key / JWT is wrong - never the device's fault: retry, bounded.
        return SendResult(Outcome.RETRY, ErrorClass.PROVIDER_AUTH, status, reason)
    if status in (400, 403, 404, 405):
        return SendResult(Outcome.PERMANENT, ErrorClass.REQUEST_INVALID, status, reason)
    if status == 429:
        return SendResult(Outcome.RETRY, ErrorClass.RATE_LIMITED, status, reason, retry_after)
    if status == 500:
        return SendResult(Outcome.RETRY, ErrorClass.PROVIDER_INTERNAL, status, reason, retry_after)
    if status == 503:
        return SendResult(Outcome.RETRY, ErrorClass.PROVIDER_UNAVAILABLE, status, reason,
                          retry_after)
    return SendResult(Outcome.RETRY, ErrorClass.UNKNOWN, status, reason, retry_after)


def next_attempt(attempts: int, now: datetime, *, retry_after: timedelta | None = None,
                 rng: random.Random | None = None) -> datetime:
    """Bounded exponential backoff with jitter; honours Retry-After."""
    base = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** max(attempts - 1, 0)))
    jitter = (rng or random).uniform(0.0, 0.2) * base.total_seconds()
    delay = base + timedelta(seconds=jitter)
    if retry_after is not None:
        delay = max(delay, retry_after)
    return now + delay


# --------------------------------------------------------------------------
# payloads - structured, versioned, deterministic; presentation strings are
# localization KEYS the app owns, never English the client must parse
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OpeningFacts:
    """Everything a payload may say - copied from the immutable event."""

    event_id: str
    watch_id: str
    term_code: str
    course_string: str
    supplement_code: str
    section_number: str
    index_number: str
    observed_at: datetime
    #: Only when Phase 6.6.1's deterministic component model found a required
    #: companion record for THIS course (e.g. 01:750:194's LB lab); else None.
    component_state: str | None = None
    component_open: int | None = None
    component_total: int | None = None
    component_as_of: datetime | None = None


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def opening_state(f: OpeningFacts) -> dict:
    """The structured state both push kinds share (and the iOS ContentState mirrors)."""
    state = {
        "v": PAYLOAD_VERSION,
        "kind": "section_opened",
        "event_id": f.event_id,
        "watch_id": f.watch_id,
        "term": f.term_code,
        "course": f.course_string,
        "section": f.section_number,
        "index": f.index_number,
        "availability": "open",
        "observed_at": _epoch(f.observed_at),
        "deep_link": f"coursepilot://watches/{f.watch_id}?event={f.event_id}",
    }
    if f.supplement_code:
        state["supplement"] = f.supplement_code
    if f.component_state is not None:
        state["required_component"] = {
            "state": f.component_state, "open": f.component_open,
            "total": f.component_total,
            "as_of": _epoch(f.component_as_of) if f.component_as_of else None}
    return state


_ALERT = {"title-loc-key": "CP_SECTION_OPENED_TITLE",
          "loc-key": "CP_SECTION_OPENED_BODY"}


def alert_payload(f: OpeningFacts) -> bytes:
    """An ordinary APNs alert. The app localizes the keys with the course and
    section as arguments - no English sentence on the wire to parse."""
    body = {"aps": {"alert": {**_ALERT, "title-loc-args": [f.course_string],
                              "loc-args": [f.course_string, f.section_number]},
                    "sound": "default", "thread-id": f"watch-{f.watch_id}"},
            "coursepilot": opening_state(f)}
    return _encode(body)


def live_activity_payload(f: OpeningFacts) -> bytes:
    """ActivityKit `end` with the final OPEN state: the watch's activity has
    done its job (Apple HIG: end a Live Activity when its event ends) and
    stays visible on the Lock Screen for LIVE_ACTIVITY_DISMISS_AFTER."""
    body = {"aps": {"timestamp": _epoch(f.observed_at), "event": "end",
                    "content-state": opening_state(f),
                    "dismissal-date": _epoch(f.observed_at + LIVE_ACTIVITY_DISMISS_AFTER),
                    "alert": {**_ALERT, "title-loc-args": [f.course_string],
                              "loc-args": [f.course_string, f.section_number]}}}
    return _encode(body)


class PayloadTooLarge(ValueError):
    pass


def _encode(body: dict) -> bytes:
    data = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    if len(data) > MAX_PAYLOAD_BYTES:
        raise PayloadTooLarge(f"{len(data)} bytes > {MAX_PAYLOAD_BYTES}")
    return data


__all__ = [
    "BACKOFF_BASE", "BACKOFF_CAP", "EVENT_TTL", "LEASE", "LIVE_ACTIVITY_DISMISS_AFTER",
    "LIVE_ACTIVITY_MAX", "MAX_ACTIVE_LIVE_ACTIVITIES", "MAX_ATTEMPTS",
    "MAX_ENDPOINTS_PER_ACCOUNT", "MAX_PAYLOAD_BYTES", "PAYLOAD_VERSION", "TOKEN_RE", "Channel",
    "DeliveryStatus", "EndpointKind", "ErrorClass", "OpeningFacts", "Outcome",
    "PayloadTooLarge", "SendResult", "SuppressedReason", "alert_payload", "classify_apns",
    "live_activity_payload", "next_attempt", "opening_state", "token_fingerprint",
    "token_hash", "valid_token"]
