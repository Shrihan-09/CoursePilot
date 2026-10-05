"""Course offering EVIDENCE for a planning term (Phase 6.5).

CoursePilot holds SOC data for the terms it has loaded (`course_offering`,
`data_source.coverage`). For a later term there is no schedule - only history.
This module never turns history into a promise:

| situation | kind | placeable |
|---|---|---|
| SOC data for the target term lists the course | `confirmed_in_term` | yes |
| SOC data for the target term is COMPLETE and omits it | `not_offered_in_term` | no |
| no complete data; offered earlier in the SAME season | `historically_offered_in_season` | yes (1) |
| offered, but only in other seasons | `offered_in_other_seasons_only` | no |
| never observed | `no_offering_evidence` | no |

(1) placeable as labelled EVIDENCE, never reported as a confirmed offering.

"Same season" means Fall vs Fall, Spring vs Spring: Rutgers departments
schedule many courses in one season only, and the loaded history (two Falls,
one Spring, one Summer, a partial Winter) is the only evidence there is. No
probability is claimed - the evidence lists the observed terms.

`rules_term` is the term whose PUBLISHED prerequisites the planner applies:
the target term itself when confirmed, else the most recent same-season term
with an offering (the evaluation is then labelled an assumption).
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.planning import OfferingEvidence, OfferingEvidenceKind
from app.models import Course, CourseOffering, DataSource
from app.services.planning.terms import season

PLACEABLE = (OfferingEvidenceKind.CONFIRMED_IN_TERM, OfferingEvidenceKind.HISTORICAL_SAME_SEASON)


class OfferingIndex:
    """All offerings of a set of courses, loaded in one query."""

    def __init__(self, session: Session, course_keys: set[str] | None = None) -> None:
        self.terms: dict[str, list[str]] = defaultdict(list)
        stmt = (select(Course.course_string, CourseOffering.term_code)
                .join(CourseOffering, CourseOffering.course_id == Course.id)
                .where(Course.supplement_code == ""))
        if course_keys is not None:
            stmt = stmt.where(Course.course_string.in_(sorted(course_keys)))
        for key, term in session.execute(stmt).all():
            if term not in self.terms[key]:
                self.terms[key].append(term)
        for key in self.terms:
            self.terms[key].sort()
        #: term -> coverage ("complete" | "partial") of the loaded SOC data.
        self.coverage: dict[str, str] = {}
        for term, coverage in session.execute(
                select(DataSource.term_code, DataSource.coverage)
                .where(DataSource.kind == "rutgers_official_api",
                       DataSource.term_code.is_not(None))).all():
            if coverage == "complete" or term not in self.coverage:
                self.coverage[term] = coverage or "partial"

    def extend(self, session: Session, course_keys: set[str]) -> None:
        missing = {k for k in course_keys if k not in self.terms}
        if missing:
            for key, term in session.execute(
                    select(Course.course_string, CourseOffering.term_code)
                    .join(CourseOffering, CourseOffering.course_id == Course.id)
                    .where(Course.course_string.in_(sorted(missing)),
                           Course.supplement_code == "")).all():
                if term not in self.terms[key]:
                    self.terms[key].append(term)
            for key in missing:
                self.terms[key].sort()

    def evidence(self, course_key: str, target_term: str) -> OfferingEvidence:
        observed = list(self.terms.get(course_key, []))
        coverage = self.coverage.get(target_term)
        same = [t for t in observed if season(t) == season(target_term) and t < target_term]
        base = dict(target_term=target_term, observed_terms=observed, same_season_terms=same,
                    most_recent_term=observed[-1] if observed else None,
                    target_term_coverage=coverage)
        if target_term in observed:
            return OfferingEvidence(kind=OfferingEvidenceKind.CONFIRMED_IN_TERM,
                                    rules_term=target_term, **base)
        if coverage == "complete":
            return OfferingEvidence(kind=OfferingEvidenceKind.NOT_OFFERED_IN_TERM, **base)
        if same:
            return OfferingEvidence(kind=OfferingEvidenceKind.HISTORICAL_SAME_SEASON,
                                    rules_term=same[-1], **base)
        if observed:
            return OfferingEvidence(kind=OfferingEvidenceKind.OTHER_SEASONS_ONLY, **base)
        return OfferingEvidence(kind=OfferingEvidenceKind.NO_EVIDENCE, **base)

    def dataset_identity(self, session: Session) -> list[str]:
        """The SOC payloads the evidence rests on (term:content-hash prefix)."""
        rows = session.execute(
            select(DataSource.term_code, DataSource.content_hash)
            .where(DataSource.kind == "rutgers_official_api", DataSource.term_code.is_not(None))
            .order_by(DataSource.term_code, DataSource.content_hash)).all()
        return [f"{t}:{h[:16]}" for t, h in rows]


__all__ = ["PLACEABLE", "OfferingIndex"]
