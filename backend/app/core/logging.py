"""Structured logging setup.

JSON in deployed environments (so logs are queryable), plain text locally
(so they are readable). Configured once from `app.main`.
"""

from __future__ import annotations

import logging
import re
import sys

from app.core.config import Environment, Settings


#: Shapes that are always credentials: OpenAI/Anthropic-style keys, bearer
#: tokens, and Authorization / API-key header lines.
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{8,}"),
    re.compile(r"(?i)((?:authorization|x-api-key|api-key|cookie)['\"]?\s*[:=]\s*['\"]?)[^'\",}\s]+"),
)
REDACTED = "[REDACTED]"


class SecretRedactionFilter(logging.Filter):
    """Last line of defence: scrub credentials from every record.

    Call sites already avoid logging keys and headers; this catches the
    mistake nobody noticed. It also scrubs the configured key values
    themselves, whatever their shape.
    """

    def __init__(self, secrets: tuple[str | None, ...] = ()) -> None:
        super().__init__()
        self._literals = tuple(s for s in secrets if s and len(s) >= 8)

    def _scrub(self, text: str) -> str:
        for literal in self._literals:
            text = text.replace(literal, REDACTED)
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(
                lambda m: (m.group(1) if m.re.groups else "") + REDACTED, text
            )
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        scrubbed = self._scrub(message)
        if scrubbed != message:
            record.msg, record.args = scrubbed, None
        for key, value in list(record.__dict__.items()):
            if isinstance(value, str) and key not in ("msg", "message"):
                cleaned = self._scrub(value)
                if cleaned != value:
                    setattr(record, key, cleaned)
        return True


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)

    # Phase 5.10: every record carries the request correlation id, injected
    # by a filter rather than by call sites - so the Degree Engine, the
    # cache and the providers are correlated too, without any of them
    # needing to know an HTTP layer exists.
    from app.core.observability import RequestIdFilter

    handler.addFilter(RequestIdFilter())
    handler.addFilter(SecretRedactionFilter(
        (settings.openai_api_key, settings.anthropic_api_key)
    ))

    if settings.coursepilot_env is Environment.LOCAL:
        handler.setFormatter(
            logging.Formatter("%(levelname)-8s [%(request_id)s] %(name)s: %(message)s")
        )
    else:
        from pythonjsonlogger import jsonlogger

        handler.setFormatter(
            jsonlogger.JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s"
            )
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    # Phase 6.9: HTTP libraries log request URLs at INFO, and an APNs URL
    # contains the device token. The OpenAI/Anthropic SDKs log request
    # options (headers included) at DEBUG. Never below WARNING.
    for name in ("httpx", "httpcore", "hpack", "h2", "openai", "anthropic"):
        logging.getLogger(name).setLevel(logging.WARNING)
