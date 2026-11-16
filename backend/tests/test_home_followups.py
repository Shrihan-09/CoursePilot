"""Home follow-ups: GPA stays out of the assistant; the profile carries the
encoded degree credit total (or null - never a guessed number)."""

from __future__ import annotations

import pathlib
import re
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.v1.routes import onboarding as routes
from app.services.planning import degree_total as dt

ASSISTANT = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "assistant"


def test_the_assistant_never_reads_the_transcript_gpa() -> None:
    """Owner decision: no "let the assistant use my grades" - the transcript
    GPA is not read anywhere in the assistant package."""
    offenders = []
    for path in ASSISTANT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"transcript_reported_gpa|latest_reported_gpa|reported_cumulative_gpa", text):
            offenders.append(path.name)
    assert offenders == []


def test_the_assistant_progress_tool_never_carries_a_gpa_value() -> None:
    from app.services.assistant.db_tools import DbAssistantTools  # noqa: F401  (import must exist)

    source = (ASSISTANT / "db_tools.py").read_text(encoding="utf-8")
    # The only GPA key the tool writes is an explicit None plus a note.
    assigned = re.findall(r'out\["gpa"\]\s*=\s*(.+)', source)
    assert assigned == ["None"]


class _Session:
    def __init__(self, version):
        self.version = version

    def get(self, _model, _id):
        return self.version


def test_profile_degree_total_is_the_encoded_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dt, "degree_total_for", lambda session, version: dt.SAS_2026_2027)
    view = routes._degree_credit_total(_Session(object()), SimpleNamespace(program_version_id=1))
    assert view is not None
    assert view.credits == Decimal(120)
    assert view.source_url.startswith("https://")


def test_profile_degree_total_is_null_when_no_source_states_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dt, "degree_total_for", lambda session, version: None)
    assert routes._degree_credit_total(_Session(object()), SimpleNamespace(program_version_id=1)) is None
    assert routes._degree_credit_total(_Session(None), SimpleNamespace(program_version_id=1)) is None
