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
from collections import deque
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

#: WHICH component of the key moved (Phase 5.9). A stale miss increments
#: `audit_cache_stale_total` once, plus one counter per component that
#: differs - so several of these can move together when a request has been
#: idle across more than one kind of change, and their sum can exceed the
#: stale total. That is deliberate: the question they answer is "what keeps
#: invalidating this cache?", not "how many misses were there".
#:
#: Three separate NAMES rather than one counter with a `cause` label,
#: because the registry deliberately has no label API (see the cardinality
#: policy below).
AUDIT_CACHE_STALE_ACADEMIC = "audit_cache_stale_academic_total"
AUDIT_CACHE_STALE_RULES = "audit_cache_stale_rules_total"
AUDIT_CACHE_STALE_ENGINE = "audit_cache_stale_engine_total"

#: The one that matters for the per-program question: a stale miss where the
#: ONLY thing that moved was the rules version. Those are the misses a
#: per-program rules version could potentially have avoided - but only
#: *potentially*, because a rule change inside the student's own program is
#: a legitimate invalidation that any design must honour. Deciding which is
#: which needs the per-program fingerprint, which costs ~14 ms and is
#: therefore measured in the benchmark rather than on the hot path.
AUDIT_CACHE_STALE_RULES_ONLY = "audit_cache_stale_rules_only_total"

#: The engine raised. Counted because "audit succeeded" and "audit happened"
#: are different facts, and a cache phase must not make a rising engine
#: failure rate invisible.
AUDIT_FAILURES = "audit_failures_total"

# --- request (Phase 5.10) -------------------------------------------------
#: Status CLASS, not status code, and certainly not path-with-parameters.
#: "2xx/4xx/5xx" is what an operator acts on; a per-path or per-status
#: counter would grow with the URL space, and a path containing a student id
#: would put an identifier in a metric name.
REQUESTS_TOTAL = "http_requests_total"
REQUESTS_2XX = "http_requests_2xx_total"
REQUESTS_4XX = "http_requests_4xx_total"
REQUESTS_5XX = "http_requests_5xx_total"
REQUEST_DURATION = "http_request_duration_ms"

# --- authentication / authorization (Phase 5.10) --------------------------
AUTHENTICATION_FAILURES = "authentication_failures_total"
AUTHORIZATION_FAILURES = "authorization_failures_total"
UNLINKED_ACCOUNT_TOTAL = "unlinked_account_total"
RATE_LIMITED_TOTAL = "rate_limited_total"

# --- explanation (Phase 5.10) ---------------------------------------------
#: The deterministic path is the authority, so it is counted as an OUTCOME
#: rather than as a failure. A high fallback rate is a signal about the
#: provider, not about academic correctness.
EXPLANATION_DETERMINISTIC = "explanation_deterministic_total"
EXPLANATION_MODEL_ATTEMPTED = "explanation_model_attempted_total"
EXPLANATION_MODEL_SUCCEEDED = "explanation_model_succeeded_total"
EXPLANATION_MODEL_FAILED = "explanation_model_failed_total"
EXPLANATION_MODEL_REJECTED = "explanation_model_rejected_total"
EXPLANATION_PROVIDER_UNAVAILABLE = "explanation_provider_unavailable_total"
EXPLANATION_NOT_GROUNDED = "explanation_not_grounded_total"

# --- search index lifecycle (Phase 5.11) ----------------------------------
#: Answers "how often are we rebuilding the BM25 index?" without reading
#: application internals - which was impossible before, because the rebuild
#: happened silently inside every request.
SEARCH_INDEX_BUILDS = "search_index_builds_total"
SEARCH_INDEX_BUILD_FAILURES = "search_index_build_failures_total"
SEARCH_INDEX_BUILD_DURATION = "search_index_build_duration_ms"
#: The number that should dominate in a healthy process.
SEARCH_INDEX_REUSE = "search_index_reuse_total"
#: A concurrent cold start where a second thread found the index already
#: published while it waited for the lock - one build, not N.
SEARCH_INDEX_CONCURRENT_SUPPRESSED = "search_index_concurrent_suppressed_total"
#: A rebuild failed and a KNOWN-OLD index was served. Never silent.
SEARCH_INDEX_STALE_SERVED = "search_index_stale_served_total"
#: Freshness could not be proven (no version table), so the index was built
#: per request - the pre-5.11 behaviour.
SEARCH_INDEX_VERSION_UNAVAILABLE = "search_index_version_unavailable_total"

# --- stage timings (Phase 5.10, Part 4) -----------------------------------
STAGE_AUTHENTICATION = "stage_authentication_ms"
STAGE_SESSION_ACQUIRE = "stage_session_acquire_ms"
STAGE_OWNERSHIP = "stage_ownership_ms"
STAGE_ACADEMIC_FINGERPRINT = "stage_academic_fingerprint_ms"
STAGE_RULES_STATE = "stage_rules_state_ms"
STAGE_CONTEXT_BUILD = "stage_context_build_ms"
STAGE_SERIALIZATION = "stage_serialization_ms"
STAGE_EVIDENCE = "stage_evidence_ms"
STAGE_RETRIEVAL = "stage_retrieval_ms"
STAGE_MODEL_CALL = "stage_model_call_ms"
STAGE_MODEL_VALIDATION = "stage_model_validation_ms"

# --- histograms (milliseconds) -------------------------------------------
AUDIT_DURATION = "audit_duration_ms"
AUDIT_CACHE_LOOKUP_DURATION = "audit_cache_lookup_duration_ms"
AUDIT_CACHE_WRITE_DURATION = "audit_cache_write_duration_ms"
ENGINE_DURATION = "audit_engine_duration_ms"


#: How many recent observations each histogram keeps for percentiles.
#: Bounded on purpose: an unbounded sample list is a slow memory leak, and
#: percentiles over the last N requests are what an operator actually wants
#: ("is it slow NOW?") rather than an average since process start.
_RESERVOIR = 512


@dataclass
class _Histogram:
    count: int = 0
    total: float = 0.0
    minimum: float = float("inf")
    maximum: float = 0.0
    #: A ring of the most recent observations. Phase 5.9 reported only
    #: count/sum/min/max and named the absence of percentiles as a
    #: limitation; a mean hides exactly the tail an operator is paged about.
    recent: deque[float] = field(default_factory=lambda: deque(maxlen=_RESERVOIR))

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)
        self.recent.append(value)

    def _percentile(self, fraction: float) -> float:
        if not self.recent:
            return 0.0
        ordered = sorted(self.recent)
        index = min(int(round(fraction * (len(ordered) - 1))), len(ordered) - 1)
        return round(ordered[index], 3)

    def snapshot(self) -> dict[str, float]:
        if not self.count:
            return {"count": 0, "mean": 0.0, "min": 0.0, "max": 0.0,
                    "p50": 0.0, "p95": 0.0, "p99": 0.0}
        return {
            "count": self.count,
            "mean": round(self.total / self.count, 3),
            "min": round(self.minimum, 3),
            "max": round(self.maximum, 3),
            # Computed over the reservoir, not all history, so the window is
            # recent. `sample` says how many observations they rest on, so
            # nobody reads a p99 built from four data points as meaningful.
            "p50": self._percentile(0.50),
            "p95": self._percentile(0.95),
            "p99": self._percentile(0.99),
            "sample": len(self.recent),
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
            return hist.snapshot() if hist else _Histogram().snapshot()

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
    "AUTHENTICATION_FAILURES",
    "AUTHORIZATION_FAILURES",
    "EXPLANATION_DETERMINISTIC",
    "EXPLANATION_MODEL_ATTEMPTED",
    "EXPLANATION_MODEL_FAILED",
    "EXPLANATION_MODEL_REJECTED",
    "EXPLANATION_MODEL_SUCCEEDED",
    "EXPLANATION_NOT_GROUNDED",
    "EXPLANATION_PROVIDER_UNAVAILABLE",
    "RATE_LIMITED_TOTAL",
    "REQUESTS_2XX",
    "REQUESTS_4XX",
    "REQUESTS_5XX",
    "REQUESTS_TOTAL",
    "REQUEST_DURATION",
    "SEARCH_INDEX_BUILDS",
    "SEARCH_INDEX_BUILD_DURATION",
    "SEARCH_INDEX_BUILD_FAILURES",
    "SEARCH_INDEX_CONCURRENT_SUPPRESSED",
    "SEARCH_INDEX_REUSE",
    "SEARCH_INDEX_STALE_SERVED",
    "SEARCH_INDEX_VERSION_UNAVAILABLE",
    "STAGE_ACADEMIC_FINGERPRINT",
    "STAGE_AUTHENTICATION",
    "STAGE_CONTEXT_BUILD",
    "STAGE_EVIDENCE",
    "STAGE_MODEL_CALL",
    "STAGE_MODEL_VALIDATION",
    "STAGE_OWNERSHIP",
    "STAGE_RETRIEVAL",
    "STAGE_RULES_STATE",
    "STAGE_SERIALIZATION",
    "STAGE_SESSION_ACQUIRE",
    "UNLINKED_ACCOUNT_TOTAL",
    "AUDIT_CACHE_INVALIDATIONS",
    "AUDIT_CACHE_LOOKUP_DURATION",
    "AUDIT_CACHE_MISSES",
    "AUDIT_CACHE_READ_FAILURES",
    "AUDIT_CACHE_STALE",
    "AUDIT_CACHE_STALE_ACADEMIC",
    "AUDIT_CACHE_STALE_ENGINE",
    "AUDIT_CACHE_STALE_RULES",
    "AUDIT_CACHE_STALE_RULES_ONLY",
    "AUDIT_CACHE_WRITE_FAILURES",
    "AUDIT_CACHE_WRITE_DURATION",
    "AUDIT_DURATION",
    "AUDIT_FAILURES",
    "ENGINE_DURATION",
    "MetricsRegistry",
    "get_metrics",
]
