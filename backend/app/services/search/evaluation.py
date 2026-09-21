"""Retrieval evaluation (Phase 5.0, Parts G and H).

Retrieval quality is a measurement, not an impression. "It looks good on a
few examples" is how a retriever ships that fails on the query types nobody
tried, so this module computes the metrics and the labelled set lives in
test data rather than in production code.

## The metrics, and what each one hides

```
Recall@K     of the known-relevant documents, how many are in the top K
Precision@K  of the top K, how many are relevant
MRR          1 / rank of the FIRST relevant result, averaged over queries
```

They fail in different directions, which is why the brief asks for more than
one:

- **Recall@K** ignores order. A relevant document at rank 10 counts the same
  as one at rank 1.
- **Precision@K** punishes a query with few relevant documents. If only one
  course is relevant, Precision@10 cannot exceed 0.1 no matter how perfect
  the ranking - so it is reported but never optimized against alone.
- **MRR** only sees the first hit. A retriever that puts one relevant course
  first and misses four others scores identically to one that finds all five
  with the same leader.

Reported per query TYPE as well as overall, because an average across types
conceals the thing worth knowing: exact-code lookup and conceptual search
succeed or fail for completely different reasons.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.services.search.contract import CourseSearcher


@dataclass(frozen=True, slots=True)
class EvalQuery:
    """One labelled query.

    `relevant` holds course keys verified by hand against the real corpus.
    `query_type` groups results so a weakness can be attributed rather than
    averaged away.
    """

    query: str
    query_type: str
    relevant: frozenset[str]
    note: str = ""


@dataclass(frozen=True, slots=True)
class QueryOutcome:
    query: str
    query_type: str
    retrieved: tuple[str, ...]
    relevant: frozenset[str]

    def recall_at(self, k: int) -> float:
        if not self.relevant:
            return 0.0
        hits = len(set(self.retrieved[:k]) & self.relevant)
        return hits / len(self.relevant)

    def precision_at(self, k: int) -> float:
        if k == 0:
            return 0.0
        return len(set(self.retrieved[:k]) & self.relevant) / k

    def reciprocal_rank(self) -> float:
        for position, course_key in enumerate(self.retrieved, start=1):
            if course_key in self.relevant:
                return 1.0 / position
        return 0.0


@dataclass(frozen=True, slots=True)
class MetricSet:
    queries: int
    recall_at_5: float
    recall_at_10: float
    precision_at_5: float
    mrr: float

    def summary(self) -> str:
        return (
            f"n={self.queries:2d}  R@5={self.recall_at_5:.3f}  "
            f"R@10={self.recall_at_10:.3f}  P@5={self.precision_at_5:.3f}  "
            f"MRR={self.mrr:.3f}"
        )


def _aggregate(outcomes: list[QueryOutcome]) -> MetricSet:
    if not outcomes:
        return MetricSet(0, 0.0, 0.0, 0.0, 0.0)
    n = len(outcomes)
    return MetricSet(
        queries=n,
        recall_at_5=sum(o.recall_at(5) for o in outcomes) / n,
        recall_at_10=sum(o.recall_at(10) for o in outcomes) / n,
        precision_at_5=sum(o.precision_at(5) for o in outcomes) / n,
        mrr=sum(o.reciprocal_rank() for o in outcomes) / n,
    )


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    retriever: str
    overall: MetricSet
    by_type: dict[str, MetricSet] = field(default_factory=dict)
    outcomes: tuple[QueryOutcome, ...] = ()

    def render(self) -> str:
        lines = [f"{self.retriever}: {self.overall.summary()}"]
        for query_type in sorted(self.by_type):
            lines.append(f"    {query_type:24s} {self.by_type[query_type].summary()}")
        return "\n".join(lines)

    def failures(self, k: int = 10) -> list[QueryOutcome]:
        """Queries that retrieved nothing relevant - the interesting ones."""
        return [o for o in self.outcomes if o.recall_at(k) == 0.0]


def evaluate(
    searcher: CourseSearcher, queries: list[EvalQuery], *, limit: int = 10
) -> EvaluationReport:
    """Run every labelled query and aggregate, overall and per type."""
    outcomes: list[QueryOutcome] = []
    for eval_query in queries:
        results = searcher.search(eval_query.query, limit=limit)
        outcomes.append(
            QueryOutcome(
                query=eval_query.query,
                query_type=eval_query.query_type,
                retrieved=tuple(r.course_key for r in results),
                relevant=eval_query.relevant,
            )
        )

    grouped: dict[str, list[QueryOutcome]] = defaultdict(list)
    for outcome in outcomes:
        grouped[outcome.query_type].append(outcome)

    return EvaluationReport(
        retriever=getattr(searcher, "name", type(searcher).__name__),
        overall=_aggregate(outcomes),
        by_type={t: _aggregate(o) for t, o in grouped.items()},
        outcomes=tuple(outcomes),
    )


__all__ = [
    "EvalQuery",
    "EvaluationReport",
    "MetricSet",
    "QueryOutcome",
    "evaluate",
]
