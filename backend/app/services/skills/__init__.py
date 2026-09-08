"""Skill loading.

NOT IMPLEMENTED YET. Contract only.

A "skill" is a versioned, on-disk bundle of structured knowledge (see
`/skills` and skills/SKILL_FORMAT.md). The loader's job is to read manifests
and hand the agent only the skills the router selected — major-specific
knowledge must never be loaded wholesale into every request.

Hard rule: skill content is *guidance and structure*, not authoritative data.
Requirement facts live in the database with provenance. A skill may say "this
program has a capstone requirement category"; it may not be the only place a
specific course-to-requirement mapping exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SkillManifest:
    skill_id: str
    kind: str  # "router" | "shared" | "major" | "tool"
    version: str
    description: str
    # Skills a major skill pulls in (e.g. a university-wide core curriculum
    # skill shared across programs).
    depends_on: list[str] = field(default_factory=list)
    # Estimated context cost, so the router can budget what it loads.
    approx_tokens: int | None = None


@runtime_checkable
class SkillRegistry(Protocol):
    def list_skills(self) -> list[SkillManifest]: ...

    def load(self, skill_id: str) -> str:
        """Return the skill body, with dependencies resolved."""
        ...
