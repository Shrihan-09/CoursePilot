"""Correctness-aware caching of deterministic degree audits (Phase 5.7).

> Caching a deterministic computation is not a performance problem. It is an
> **invalidation** problem wearing a performance problem's clothes.

The engine is already deterministic: same inputs, same result, always. So
"is this cached result still correct?" reduces entirely to "are the inputs
still the same?" - and that is the only question this module answers.

## The invariant

**A cached audit may be returned only when the cache key represents the same
academic facts and rule state that would be supplied to the Degree Engine
for a fresh computation.**

Restated as the thing that must be true on every hit:

```
same student academic state
+ same applicable program / requirements / eligibility / rules
+ same engine semantics
= same audit result
```

## Invalidation by construction, not by discipline

The tempting design is version counters that writers bump. It fails the
same way every time: someone adds a write path and forgets the bump, and the
system then serves a **stale academic result** - the worst possible bug
class here, because it is silent and it is about someone's degree.

So the key is a hash of the input rows themselves. Change a grade, recurate
a requirement, swap the objective, and the hash changes; the stored row no
longer matches and is never read. **No invalidation call is required for
correctness.** `invalidate_student_audit` exists for promptness and storage
hygiene (Part 9), not for correctness - forgetting it costs a wasted
recomputation, never a wrong answer.

## What is fingerprinted

Read out of `DegreeAuditEngine.audit()` rather than assumed:

| input | why it can change the result |
|---|---|
| `Student.catalog_year`, `program_version_id` | selects the ruleset; a mismatch is a blocking finding |
| `StudentCourse` rows | status, term, grade, credits - the facts themselves |
| joined `Course` rows | course_string drives tie-breaking; credits drive credit rules |
| `ProgramVersion` | catalog year, credit range, sharing policy, curation status |
| `Requirement` tree | counts, credits, categories, types, sort order |
| `RequirementCourseOption` | which courses are eligible, and in which category |
| `ProgramRule` | exclusions that change which credits count |
| engine policy | objective, category strategy, baseline semantics |

Columns are enumerated **reflectively** from the mapped table. A column
added to `Requirement` next year is covered automatically; a hand-written
list would silently omit it, and silently omitting an input from a cache key
is precisely how a stale audit gets served.

Timestamps are excluded - they are the only columns that change without
changing meaning, and including them would cause spurious misses on a no-op
UPDATE.

### Rejected: `(count(*), max(updated_at))`

Far cheaper than hashing 800 eligibility rows, and wrong. `updated_at` is
maintained by the ORM, so recuration applied as raw SQL - which is exactly
how a hurried catalog fix gets made - would leave it untouched and the audit
permanently stale. Measured at ~10 ms, the exact hash buys guaranteed
correctness, and this is not a place to trade that away.

## Engine version

Semantics can change with no database row changing at all: a different
objective, a different category strategy, different tie-breaking. So the key
carries an engine version with two halves:

```
AUDIT_ENGINE_VERSION            explicit, bumped by a human
+ objective.name / strategy.name  read from the live policy objects
```

The second half is the safety net. Swapping `DEFAULT_OBJECTIVE` changes the
key whether or not anyone remembered the constant. The explicit half covers
what the names cannot see - a bug fix inside the allocator, a change to
baseline semantics - and **must** be bumped when audit output can change for
unchanged inputs.

## Failure behaviour

Every cache operation is best-effort. A read that fails, a row that will not
deserialize, a write that is refused - each falls through to computing a
fresh audit. **Cache failure is never audit failure**, and a stale result is
never served as a consolation for a broken cache.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
import zlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.audit import DegreeAuditResult
from app.core.metrics import (
    AUDIT_CACHE_INVALIDATIONS,
    AUDIT_CACHE_LOOKUP_DURATION,
    AUDIT_CACHE_READ_FAILURES,
    AUDIT_CACHE_STALE,
    AUDIT_CACHE_WRITE_DURATION,
    AUDIT_CACHE_WRITE_FAILURES,
    get_metrics,
)
from app.models import (
    Course,
    Program,
    ProgramRule,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    Student,
    StudentAuditCache,
    StudentCourse,
)

logger = logging.getLogger(__name__)

#: Bump when the engine can produce a different audit for identical inputs.
#:
#: That means: allocation changes, evaluation changes, baseline semantics,
#: tie-breaking, credit accounting, finding text that clients may rely on.
#: It does NOT mean refactors that provably cannot change output. When in
#: doubt, bump - the cost is one recomputation per student, and the cost of
#: not bumping is telling a student the wrong thing about their degree.
AUDIT_ENGINE_VERSION = "5.7.0"

#: Columns whose value changes without the meaning changing.
_IGNORED_COLUMNS = frozenset({"created_at", "updated_at"})


def engine_version() -> str:
    """The explicit version, plus the identities of the live policy objects.

    Reading the policy names means swapping the objective or the category
    strategy invalidates the cache on its own, without depending on anyone
    remembering `AUDIT_ENGINE_VERSION`.
    """
    from app.services.audit.categories import DEFAULT_STRATEGY
    from app.services.audit.optimizer import DEFAULT_OBJECTIVE

    raw = (
        f"{AUDIT_ENGINE_VERSION}|"
        f"objective={getattr(DEFAULT_OBJECTIVE, 'name', '?')}|"
        f"strategy={getattr(DEFAULT_STRATEGY, 'name', '?')}"
    )
    # Hashed to fit String(64) whatever the policy names grow into; the raw
    # form is logged at debug when a miss needs explaining.
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _hash_entity(hasher, row) -> None:
    """Hash every mapped column except timestamps.

    Reflective on purpose: a column added later is covered without anyone
    editing this function, and forgetting to add one here would mean stale
    audits rather than a visible error.
    """
    for column in row.__table__.columns:
        if column.name in _IGNORED_COLUMNS:
            continue
        value = getattr(row, column.name)
        hasher.update(f"{column.name}={'' if value is None else value}|".encode())
    hasher.update(b"\n")


def _safe_rollback(session) -> None:
    """Clear an aborted transaction so the caller can keep working.

    Every cache failure path calls this. A database error leaves a
    PostgreSQL transaction unusable, and the Degree Engine runs on the same
    session immediately afterwards.
    """
    try:
        session.rollback()
    except Exception:
        logger.debug("audit_cache_rollback_failed", exc_info=True)


def _hash_tuples(hasher, rows) -> None:
    for row in rows:
        hasher.update(
            "|".join("" if v is None else str(v) for v in row).encode()
        )
        hasher.update(b"\n")


def academic_fingerprint(session: Session, student: Student) -> str:
    """The student's own audit inputs.

    Ordered deterministically by natural key - never by UUID, which is
    stable within one database and meaningless across a re-ingest, so a
    UUID ordering would make the fingerprint non-reproducible.
    """
    hasher = hashlib.sha256()
    hasher.update(
        f"catalog_year={student.catalog_year}|"
        f"program_version={student.program_version_id}\n".encode()
    )
    rows = session.execute(
        select(
            StudentCourse.course_id,
            StudentCourse.term_code,
            StudentCourse.status,
            StudentCourse.grade,
            StudentCourse.credits_earned,
            StudentCourse.source_kind,
            Course.course_string,
            Course.supplement_code,
            Course.title,
            Course.credits,
        )
        .join(Course, Course.id == StudentCourse.course_id)
        .where(StudentCourse.student_id == student.id)
        .order_by(
            Course.course_string, Course.supplement_code, StudentCourse.term_code
        )
    ).all()
    _hash_tuples(hasher, rows)
    return hasher.hexdigest()


def rules_fingerprint(session: Session, program_version_id: uuid.UUID) -> str:
    """The rule state that binds this student.

    This is the fingerprint that makes recuration safe. A student's facts can
    be untouched for a year while the audit changes underneath them because a
    requirement was re-read from the catalog - so a student-only cache key is
    not merely imprecise, it is wrong.
    """
    hasher = hashlib.sha256()

    version = session.get(ProgramVersion, program_version_id)
    if version is None:
        # No version, no rules to fingerprint. The engine raises on this, and
        # a caller that reaches here will get a fresh (failing) audit rather
        # than a cached anything.
        return hasher.hexdigest()
    _hash_entity(hasher, version)

    # The Program row, added in Phase 5.8 after tracing the engine's inputs.
    # `program_name`, `program_code` and `degree_type` are fields of
    # DegreeAuditResult, so renaming a program changes the audit - and Phase
    # 5.7's fingerprint did not hash this table, which meant the cache served
    # the OLD name indefinitely. Demonstrated, then fixed.
    program = session.get(Program, version.program_id)
    if program is not None:
        _hash_entity(hasher, program)

    for requirement in session.scalars(
        select(Requirement)
        .where(Requirement.program_version_id == program_version_id)
        .order_by(Requirement.code)
    ):
        _hash_entity(hasher, requirement)

    # Eligibility, scoped through Requirement so it covers exactly the rows
    # `_load_eligibility` would read.
    _hash_tuples(
        hasher,
        session.execute(
            select(
                Requirement.code,
                RequirementCourseOption.course_id,
                RequirementCourseOption.category,
            )
            .join(
                Requirement,
                Requirement.id == RequirementCourseOption.requirement_id,
            )
            .where(Requirement.program_version_id == program_version_id)
            .order_by(
                Requirement.code,
                RequirementCourseOption.course_id,
                RequirementCourseOption.category,
            )
        ).all(),
    )

    for rule in session.scalars(
        select(ProgramRule)
        .where(ProgramRule.program_version_id == program_version_id)
        .order_by(ProgramRule.code)
    ):
        _hash_entity(hasher, rule)

    return hasher.hexdigest()


#: zlib level 6. Measured in Phase 5.8: level 1 gives 5,035 bytes for
#: 0.05 ms, level 6 gives 3,487 for 0.17 ms, level 9 gives 3,480 for 0.28 ms.
#: Level 9 buys 7 bytes for 65% more CPU; level 1 costs 1.5 KB to save
#: 0.12 ms. Six is the knee of that curve.
_COMPRESSION_LEVEL = 6


def encode_result(result: DegreeAuditResult) -> bytes:
    """Serialize for storage: the EXACT json a fresh audit would emit.

    Compressed because the cold path is dominated by payload size, not by
    engine time - 24,277 raw bytes insert in ~46 ms on this PostgreSQL while
    3,487 compressed bytes insert in ~2.2 ms. Compression costs 0.15 ms.

    Structural reduction was considered first and rejected on measurement:
    field names are 39.2% of the payload, duplicate string values 13.2%, and
    null/empty fields 15.3% - which is exactly the redundancy a hand-written
    compact schema would target, and exactly what zlib already removes
    (85.6%). A compact schema would buy less, and would cost a second
    representation of academic data to keep correct.
    """
    return zlib.compress(result.model_dump_json().encode(), _COMPRESSION_LEVEL)


def decode_result(blob: bytes) -> DegreeAuditResult:
    """Inverse of `encode_result`. Raises on anything it does not recognise."""
    return DegreeAuditResult.model_validate_json(zlib.decompress(blob).decode())


@dataclass(frozen=True, slots=True)
class AuditCacheKey:
    """Everything that must match for a stored audit to still be correct."""

    academic: str
    rules: str
    engine: str

    @classmethod
    def compute(cls, session: Session, student: Student) -> AuditCacheKey:
        from app.services.audit.rules_state import rules_token

        return cls(
            academic=academic_fingerprint(session, student),
            # Phase 5.8: the trigger-maintained rules version where it is
            # available, the full Phase 5.7 fingerprint otherwise. Both are
            # correct; one is ~14 ms cheaper on every audit.
            rules=rules_token(session, student.program_version_id),
            engine=engine_version(),
        )

    def matches(self, row: StudentAuditCache) -> bool:
        return (
            row.academic_fingerprint == self.academic
            and row.rules_token == self.rules
            and row.engine_version == self.engine
        )


def read_cached_audit(
    session: Session, student: Student, key: AuditCacheKey
) -> DegreeAuditResult | None:
    """Return the stored audit if every fingerprint still matches.

    Never raises. A corrupt row, a schema that has moved on, an unreadable
    table - all of them mean "no cache", not "no audit".
    """
    metrics = get_metrics()
    started = time.perf_counter()
    try:
        row = session.get(StudentAuditCache, student.id)
    except Exception:
        metrics.increment(AUDIT_CACHE_READ_FAILURES)
        logger.warning("audit_cache_read_failed", exc_info=True)
        # The rollback is the load-bearing part, not the `except`.
        # PostgreSQL aborts the whole transaction on a failed statement, so
        # without this every subsequent query in this session - including the
        # Degree Engine's - fails with InFailedSqlTransaction. Swallowing the
        # exception without clearing the transaction would turn "the cache is
        # broken" into "the audit is broken", which is precisely what must
        # not happen.
        _safe_rollback(session)
        return None

    if row is None:
        return None
    if not key.matches(row):
        # The common, healthy miss: inputs moved. Counted separately from a
        # cold miss because they mean different things operationally - many
        # STALE misses means the rules keep moving, many COLD misses means
        # the population is growing or something is clearing the table.
        #
        # Deliberately not deleted here: the recomputation overwrites it, and
        # a read path that writes is a read path that can fail in new ways.
        metrics.increment(AUDIT_CACHE_STALE)
        logger.debug("audit_cache_stale")
        return None

    try:
        result = decode_result(row.result_blob)
    except Exception:
        # Compressed under a scheme this process does not recognise, or
        # genuinely corrupt. Either way: recompute. No rollback needed - this
        # failure is in Python, and the transaction is still healthy.
        metrics.increment(AUDIT_CACHE_READ_FAILURES)
        logger.warning("audit_cache_deserialize_failed", exc_info=True)
        return None

    metrics.observe(
        AUDIT_CACHE_LOOKUP_DURATION, (time.perf_counter() - started) * 1000
    )
    return result


def write_cached_audit(
    session: Session,
    student: Student,
    key: AuditCacheKey,
    result: DegreeAuditResult,
) -> bool:
    """Store an audit. Returns whether it was stored; never raises.

    Concurrency: several requests may miss simultaneously and all compute.
    They each write the **same bytes**, because the engine is deterministic
    over identical inputs and the fingerprints prove the inputs were
    identical. So the race is benign and no lock is needed - the cost is
    duplicated CPU under a cold key, and the alternative (distributed
    locking) buys nothing here and can fail in ways that stall a request.
    """
    metrics = get_metrics()
    started = time.perf_counter()
    try:
        payload = encode_result(result)
    except Exception:
        metrics.increment(AUDIT_CACHE_WRITE_FAILURES)
        logger.warning("audit_cache_serialize_failed", exc_info=True)
        return False

    try:
        row = session.get(StudentAuditCache, student.id)
        if row is None:
            session.add(
                StudentAuditCache(
                    student_id=student.id,
                    academic_fingerprint=key.academic,
                    rules_token=key.rules,
                    engine_version=key.engine,
                    result_blob=payload,
                )
            )
        else:
            row.academic_fingerprint = key.academic
            row.rules_token = key.rules
            row.engine_version = key.engine
            row.result_blob = payload
        session.commit()
        metrics.observe(
            AUDIT_CACHE_WRITE_DURATION, (time.perf_counter() - started) * 1000
        )
        return True
    except Exception:
        # A losing writer in a race, a read-only replica, a full disk. The
        # caller already has a correct audit; the cache simply did not take.
        metrics.increment(AUDIT_CACHE_WRITE_FAILURES)
        logger.warning("audit_cache_write_failed", exc_info=True)
        _safe_rollback(session)
        return False


def invalidate_student_audit(session: Session, student_id: uuid.UUID) -> bool:
    """Drop a student's cached audit. Returns whether a row was removed.

    **Not required for correctness** - the fingerprints already guarantee a
    changed input is never served from cache. This exists so a future
    academic-record mutation endpoint has an obvious, explicit place to say
    "this is now stale", reclaiming the row immediately instead of at the
    next read.

    Deliberately does not commit: a mutation should invalidate inside its own
    transaction, so the record change and the invalidation land together.

    Academic data is untouched. This removes derived state only.
    """
    try:
        row = session.get(StudentAuditCache, student_id)
        if row is None:
            return False
        session.delete(row)
        session.flush()
        get_metrics().increment(AUDIT_CACHE_INVALIDATIONS)
        logger.info("audit_cache_invalidated")
        return True
    except Exception:
        logger.warning("audit_cache_invalidate_failed", exc_info=True)
        _safe_rollback(session)
        return False


__all__ = [
    "AUDIT_ENGINE_VERSION",
    "AuditCacheKey",
    "academic_fingerprint",
    "decode_result",
    "encode_result",
    "engine_version",
    "invalidate_student_audit",
    "read_cached_audit",
    "rules_fingerprint",
    "write_cached_audit",
]
