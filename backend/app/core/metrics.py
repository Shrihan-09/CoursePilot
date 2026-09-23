"""Minimal in-process metrics (Phase 5.8).

No metrics library existed, and this phase is not the place to adopt one. So
this is the smallest thing that answers the questions actually asked: what
is the cache hit rate, and how often does the cache fail?

## Deliberately small

Counters and histograms, in memory, per process. The same limitation the
rate limiter carries and documents (`app/api/security.py`): it does not
survive a restart and does not aggregate across workers, so N workers means
N partial views. That is fine for the question "is the cache working?" and
is **not** a production monitoring system. A real deployment exports to
Prometheus or similar; the point of this module is that adopting one is a
decision on its own, not a side effect of a caching phase.

## Cardinality policy

Metric names are **constants declared in this file**. There is no API for
attaching arbitrary labels, and that is on purpose: an unbounded label set
is the standard way metrics turn into an outage, and a label containing a
student identifier would turn a counter into a disclosure.

> **Never a student id, external ref, provider subject, JWT or audit content
> in a metric name.** There is nowhere to put one, which is stronger than a
> rule saying not to.

Histograms keep count/sum/min/max rather than buckets - enough for "did the
warm path get faster", not enough for percentiles, and honest about it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

# --- counters -------------------------------------------------------------
AUDIT_CACHE_HITS = "audit_cache_hits_total"
AUDIT_CACHE_MISSES = "audit_cache_misses_total"
AUDIT_CACHE_READ_FAILURES = "audit_cache_read_failures_total"
AUDIT_CACHE_WRITE_FAILURES = "audit_cache_write_failures_total"
AUDIT_CACHE_INVALIDATIONS = "audit_cache_invalidations_total"
#: A miss caused by inputs having changed, as distinct from a cold cache.
#: Separated because they mean different things operationally: many stale
#: misses means the rules keep moving, many cold misses means the cache is
#: being cleared or the population is growing.
AUDIT_CACHE_STALE = "audit_cache_stale_total"

# --- histograms (milliseconds) -------------------------------------------
AUDIT_DURATION = "audit_duration_ms"
AUDIT_CACHE_LOOKUP_DURATION = "audit_cache_lookup_duration_ms"
AUDIT_CACHE_WRITE_DURATION = "audit_cache_write_duration_ms"
ENGINE_DURATION = "audit_engine_duration_ms"


@dataclass
class _Histogram:
    count: int = 0
    total: float = 0.0
    minimum: float = float("inf")
    maximum: float = 0.0

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)

    def snapshot(self) -> dict[str, float]:
        if not self.count:
            return {"count": 0, "mean": 0.0, "min": 0.0, "max": 0.0}
        return {
            "count": self.count,
            "mean": round(self.total / self.count, 3),
            "min": round(self.minimum, 3),
            "max": round(self.maximum, 3),
        }


@dataclass
class MetricsRegistry:
    """Thread-safe because audits run in FastAPI's worker threadpool.

    Counters are incremented from several threads at once, and `+= 1` on a
    dict entry is not atomic in the presence of a rehash. A lock around
    integer arithmetic is cheap next to a degree audit.
    """

    _counters: dict[str, int] = field(default_factory=dict)
    _histograms: dict[str, _Histogram] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def observe(self, name: str, milliseconds: float) -> None:
        with self._lock:
            self._histograms.setdefault(name, _Histogram()).observe(milliseconds)

    def counter(self, name: str) -> int:
        with self._lock:
            return self._counters.get(name, 0)

    def histogram(self, name: str) -> dict[str, float]:
        with self._lock:
            hist = self._histograms.get(name)
            return hist.snapshot() if hist else {"count": 0, "mean": 0.0, "min": 0.0, "max": 0.0}

    def snapshot(self) -> dict[str, object]:
        """Everything, for an operator. Contains no identifiers by design."""
        with self._lock:
            counters = dict(self._counters)
            histograms = {k: v.snapshot() for k, v in self._histograms.items()}

        hits = counters.get(AUDIT_CACHE_HITS, 0)
        misses = counters.get(AUDIT_CACHE_MISSES, 0)
        total = hits + misses
        return {
            "counters": counters,
            "histograms": histograms,
            # Derived here rather than by the reader, so "hit rate" has one
            # definition. None, not 0.0, when nothing has been served - a
            # cache with no traffic has no hit rate, and reporting 0% would
            # read as a broken cache.
            "cache_hit_rate": round(hits / total, 4) if total else None,
            "cache_requests_total": total,
        }

    def reset(self) -> None:
        """Test hook. Not called by application code."""
        with self._lock:
            self._counters.clear()
            self._histograms.clear()


_registry = MetricsRegistry()


def get_metrics() -> MetricsRegistry:
    return _registry


__all__ = [
    "AUDIT_CACHE_HITS",
    "AUDIT_CACHE_INVALIDATIONS",
    "AUDIT_CACHE_LOOKUP_DURATION",
    "AUDIT_CACHE_MISSES",
    "AUDIT_CACHE_READ_FAILURES",
    "AUDIT_CACHE_STALE",
    "AUDIT_CACHE_WRITE_FAILURES",
    "AUDIT_CACHE_WRITE_DURATION",
    "AUDIT_DURATION",
    "ENGINE_DURATION",
    "MetricsRegistry",
    "get_metrics",
]
