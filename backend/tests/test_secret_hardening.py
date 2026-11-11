"""Secret-leak hardening: fail fast without OPENAI_API_KEY, never log credentials.

Fake credentials are assembled at runtime so no literal in this file looks
like a real key to a secret scanner.
"""

from __future__ import annotations

import io
import logging

import pytest

from app.core.config import MissingSecretError, Settings, require_openai_key
from app.core.logging import REDACTED, SecretRedactionFilter

FAKE_KEY = "sk-" + "test" + "0" * 24
FAKE_TOKEN = "eyJ" + "a" * 30


def _settings(**overrides) -> Settings:
    base = {"llm_provider": "echo", "explanation_provider": "none",
            "assistant_provider": "fake", "openai_api_key": None}
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.mark.parametrize("setting", ["llm_provider", "explanation_provider",
                                     "assistant_provider"])
def test_openai_provider_without_key_refuses_to_start(setting):
    with pytest.raises(MissingSecretError) as exc:
        require_openai_key(_settings(**{setting: "openai"}))
    message = str(exc.value)
    assert "OPENAI_API_KEY is not set" in message
    assert f"{setting.upper()}=openai" in message


def test_blank_key_counts_as_missing():
    with pytest.raises(MissingSecretError):
        require_openai_key(_settings(assistant_provider="openai", openai_api_key="   "))


def test_key_present_or_no_openai_provider_starts():
    require_openai_key(_settings(assistant_provider="openai", openai_api_key=FAKE_KEY))
    require_openai_key(_settings())


def test_refusal_message_never_contains_a_value():
    settings = _settings(assistant_provider="openai", openai_api_key="",
                         anthropic_api_key=FAKE_KEY)
    with pytest.raises(MissingSecretError) as exc:
        require_openai_key(settings)
    assert FAKE_KEY not in str(exc.value)


def _capture(filter_: logging.Filter):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(filter_)
    handler.setFormatter(logging.Formatter("%(message)s %(detail)s"))
    log = logging.getLogger("secret-hardening-test")
    log.handlers[:] = [handler]
    log.propagate = False
    log.setLevel(logging.DEBUG)
    return log, stream


@pytest.mark.parametrize("line", [
    "calling with key {key}",
    "headers={{'Authorization': 'Bearer {token}'}}",
    "Authorization: Bearer {token}",
    "x-api-key: {key}",
])
def test_redaction_filter_scrubs_credentials(line):
    log, stream = _capture(SecretRedactionFilter())
    log.info(line.format(key=FAKE_KEY, token=FAKE_TOKEN), extra={"detail": ""})
    out = stream.getvalue()
    assert FAKE_KEY not in out and FAKE_TOKEN not in out
    assert REDACTED in out


def test_redaction_filter_scrubs_configured_literal_args_and_extras():
    literal = "opaque-provider-secret-value"
    log, stream = _capture(SecretRedactionFilter((literal,)))
    log.warning("value %s", literal, extra={"detail": f"extra {literal}"})
    out = stream.getvalue()
    assert literal not in out
    assert out.count(REDACTED) == 2


def test_redaction_filter_leaves_ordinary_messages_alone():
    log, stream = _capture(SecretRedactionFilter((FAKE_KEY,)))
    log.info("planned %d semesters for %s", 8, "CS B.S.", extra={"detail": "ok"})
    assert stream.getvalue().strip() == "planned 8 semesters for CS B.S. ok"


def test_sdk_loggers_never_below_warning():
    from app.core.logging import configure_logging

    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    try:
        configure_logging(_settings(log_level="DEBUG", coursepilot_env="local"))
        assert any(isinstance(f, SecretRedactionFilter)
                   for h in root.handlers for f in h.filters)
        for name in ("openai", "anthropic", "httpx", "httpcore"):
            assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING
    finally:
        root.handlers[:], _ = saved[0], root.setLevel(saved[1])


def _guard():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "check_private_files.py"
    spec = importlib.util.spec_from_file_location("check_private_files", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_file_guard_blocks_env_har_state_and_private_folder():
    blocked = _guard().blocked([
        ".env", "backend/.env", "frontend/.env.local", ".env.production",
        "capture.har", "browser_state.json", "data/private/x.pdf",
        "coursepilot-private/transcript.pdf", "nested/coursepilot-private/a.txt",
    ])
    assert len(blocked) == 9


def test_private_file_guard_allows_ordinary_files_and_env_example():
    assert _guard().blocked([".env.example", "backend/.env.example", "docs/SECURITY.md",
                             "backend/app/core/config.py", "environment.md"]) == []
