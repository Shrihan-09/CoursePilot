"""First-party feedback aggregates and the ONLY workload/difficulty wording (7.2.4).

Privacy: fewer than MIN_SAMPLE responses return NOT_ENOUGH_DATA with no
distribution and no median, so no individual answer can be inferred.
Honesty: wording exists only for an AVAILABLE aggregate and is phrased as what
students REPORTED, with the sample size. Missing data is never "easy" or "light".
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.intelligence import (
    AggregateState,
    Confidence,
    Dimension,
    FeedbackAggregate,
    Provenance,
)
from app.models import CourseFeedback

MIN_SAMPLE = 5
SOURCE = "coursepilot_feedback"


def confidence(n: int) -> Confidence:
    if n < MIN_SAMPLE:
        return Confidence.NONE
    if n < 15:
        return Confidence.LOW
    if n < 40:
        return Confidence.MODERATE
    return Confidence.HIGH


def wording(dimension: Dimension, state: AggregateState, middle: float | None, n: int) -> str:
    if state is AggregateState.NOT_ENOUGH_DATA or middle is None:
        return "Not enough data"
    level = "Lower" if middle < 2.5 else "Moderate" if middle <= 3.5 else "Higher"
    noun = "workload" if dimension is Dimension.WORKLOAD else "difficulty"
    return f"{level} reported {noun} ({n} student reports)"


def aggregate(session: Session, course: str, dimension: Dimension, *,
              instructor_key: str | None = None) -> FeedbackAggregate:
    stmt = select(CourseFeedback).where(CourseFeedback.course_string == course)
    if instructor_key is not None:
        stmt = stmt.where(CourseFeedback.instructor_key == instructor_key)
    rows = list(session.scalars(stmt))
    values = [getattr(r, dimension.value) for r in rows]
    n = len(values)
    state = AggregateState.AVAILABLE if n >= MIN_SAMPLE else AggregateState.NOT_ENOUGH_DATA
    terms = sorted(r.term_code for r in rows)
    middle = float(median(values)) if state is AggregateState.AVAILABLE else None
    latest: datetime | None = max((r.submitted_at for r in rows), default=None)
    return FeedbackAggregate(
        course=course, instructor_key=instructor_key, dimension=dimension, state=state,
        distribution=dict(sorted(Counter(values).items())) if state is AggregateState.AVAILABLE
        else None,
        median=middle,
        provenance=Provenance(
            source=SOURCE, sample_size=n if state is AggregateState.AVAILABLE else 0,
            window_start=terms[0] if terms and state is AggregateState.AVAILABLE else None,
            window_end=terms[-1] if terms and state is AggregateState.AVAILABLE else None,
            confidence=confidence(n),
            updated_at=latest if state is AggregateState.AVAILABLE else None),
        wording=wording(dimension, state, middle, n))


__all__ = ["MIN_SAMPLE", "aggregate", "confidence", "wording"]
