"""Conservative instructor identity (Phase 7.2.4).

SOC lists instructors as "LAST, FIRST", "LAST, F" or just "LAST". An external
profile is attached ONLY through a human-verified InstructorExternalIdentity
row for the same normalized Rutgers name AND subject. Name similarity alone is
never enough: "SMITH, J" is not any particular Smith, and two profiles with
the same name are AMBIGUOUS. When in doubt, nothing is attached.
"""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.intelligence import IdentityMatch
from app.models import InstructorExternalIdentity


def instructor_key(rutgers_name: str) -> str:
    """'Smith, John  A.' -> 'SMITH|JOHN A'. Deterministic; no guessing."""
    text = unicodedata.normalize("NFKD", rutgers_name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z,\s'-]", " ", text).upper()
    last, _, first = text.partition(",")
    last = " ".join(last.split())
    first = " ".join(first.split())
    return f"{last}|{first}" if first else last


def is_specific(key: str) -> bool:
    """A full first name is present (not an initial, not last-name only)."""
    _, _, first = key.partition("|")
    given = first.split(" ")[0] if first else ""
    return len(given) > 1


def resolve(session: Session, rutgers_name: str, subject_code: str, source: str,
            candidate_ids: list[str]) -> tuple[IdentityMatch, str | None]:
    """(match state, external id or None). Only VERIFIED ever returns an id."""
    key = instructor_key(rutgers_name)
    verified = session.scalar(select(InstructorExternalIdentity).where(
        InstructorExternalIdentity.instructor_key == key,
        InstructorExternalIdentity.subject_code == subject_code,
        InstructorExternalIdentity.source == source))
    if verified is not None and is_specific(key):
        return IdentityMatch.VERIFIED, verified.external_id
    if not candidate_ids:
        return IdentityMatch.NONE, None
    if len(candidate_ids) > 1 or not is_specific(key):
        return IdentityMatch.AMBIGUOUS, None
    return IdentityMatch.UNVERIFIED, None


__all__ = ["instructor_key", "is_specific", "resolve"]
