"""Deterministic audit-cache workload (Phase 5.9, Parts 3-4).

> The question this exists to answer: **when one rule changes, how much
> recomputation does the global rules version cause that a per-program
> version could have avoided?**

Run against a throwaway COPY of the development database:

```
python -m tests.benchmarks.audit_cache_workload
```

## Why this is a benchmark and not a test

It measures rather than asserts. Latencies are machine-dependent, so making
them assertions would produce a suite that fails when a laptop is busy. The
*correctness* properties it exercises are covered by the real tests; this
file exists to produce numbers for a decision.

## How "unnecessary" is determined

A global rules-version bump invalidates every student. Some of those
invalidations are legitimate - the changed rule belonged to that student's
own program version - and some are collateral. Telling them apart needs a
**per-program** view of rule state, and one already exists: Phase 5.7's
`rules_fingerprint`, still present as the fallback path.

So the oracle is the fingerprint:

```
cold (no cached row)                                      -> unavoidable
academic or engine component moved                        -> NECESSARY
rules ONLY  AND  per-program fingerprint changed          -> NECESSARY
rules ONLY  AND  per-program fingerprint unchanged        -> COLLATERAL
```

Only the last bucket is what per-program versioning could avoid. The first
version of this benchmark counted cold starts and engine-version bumps as
collateral too, and reported 71 avoidable misses where the real figure was
30. The cause is read from the production stale counters per call rather
than inferred from the scenario, so the classification cannot drift from
what the cache actually did.

That is an honest measurement rather than a guess, and it costs nothing in
production because it runs only here.

## Determinism

Fixed student/program counts, fixed mutation sequence, no randomness. Two
runs on the same database produce the same invalidation counts; only the
timings move.
"""

from __future__ import annotations

import datetime as dt
import decimal
import os
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.metrics import (
    AUDIT_CACHE_HITS,
    AUDIT_CACHE_MISSES,
    AUDIT_CACHE_STALE_ACADEMIC,
    AUDIT_CACHE_STALE_ENGINE,
    AUDIT_CACHE_STALE_RULES,
    AUDIT_CACHE_STALE_RULES_ONLY,
    get_metrics,
)
from app.models import (
    Course,
    DataSource,
    Program,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    School,
    Student,
    StudentCourse,
    Subject,
    UserAccount,
)
from app.services.audit.cache import rules_fingerprint
from app.services.audit.cached_audit import audit_with_cache

#: Deliberately small and stated as such. See "Limitations" in the report.
#:
#: PROGRAMS is the parameter that matters, and it is overridable because the
#: collateral fraction is a direct function of it: a rule change in one
#: program makes (P-1)/P of the population collateral. Reporting a single
#: number for one arbitrary P would look like an empirical finding about
#: CoursePilot when it is really a property of the chosen fixture. Sweeping
#: it shows the SHAPE of the cost, which is what a decision needs.
PROGRAMS = int(os.environ.get("BENCH_PROGRAMS", "4"))
STUDENTS_PER_PROGRAM = int(os.environ.get("BENCH_STUDENTS", "5"))
COURSES_PER_PROGRAM = 6


# --------------------------------------------------------------------------
# fixture construction
# --------------------------------------------------------------------------


def _source(session: Session) -> DataSource:
    suffix = uuid.uuid4().hex[:8]
    source = DataSource(
        kind="manual_curation",
        url=f"synthetic://coursepilot/bench/{suffix}",
        content_hash=suffix,
        retrieved_at=dt.datetime.now(dt.UTC),
        version=1,
    )
    session.add(source)
    session.flush()
    return source


def _course(session: Session, source: DataSource) -> Course:
    unit, subject, number = "01", uuid.uuid4().hex[:6], uuid.uuid4().hex[:6]
    subject_row = Subject(
        code=subject, offering_unit_code=unit, description="s", source_id=source.id
    )
    session.add(subject_row)
    session.flush()
    course = Course(
        offering_unit_code=unit, subject_code=subject, course_number=number,
        supplement_code="", course_string=f"{unit}:{subject}:{number}",
        title="Benchmark Course", credits=decimal.Decimal("4.0"),
        subject_id=subject_row.id, source_id=source.id,
    )
    session.add(course)
    session.flush()
    return course


@dataclass
class ProgramFixture:
    version_id: uuid.UUID
    program_id: uuid.UUID
    requirement_id: uuid.UUID
    student_ids: list[uuid.UUID] = field(default_factory=list)
    spare_course_ids: list[uuid.UUID] = field(default_factory=list)


def build(session: Session) -> list[ProgramFixture]:
    """Several programs, each with its own students, rules and eligibility."""
    fixtures: list[ProgramFixture] = []
    for _ in range(PROGRAMS):
        source = _source(session)
        suffix = uuid.uuid4().hex[:8]
        school = School(code=f"B{suffix}", name="Bench School",
                        campus_code="NB", source_id=source.id)
        session.add(school)
        session.flush()
        program = Program(school_id=school.id, code=suffix,
                          name="Bench Program", degree_type="BA",
                          source_id=source.id)
        session.add(program)
        session.flush()
        version = ProgramVersion(program_id=program.id,
                                 catalog_year="2026-2027", source_id=source.id)
        session.add(version)
        session.flush()
        requirement = Requirement(
            program_version_id=version.id, code=f"REQ_{suffix}",
            name="Core", requirement_type="choose_n", min_count=2,
            sort_order=1, source_id=source.id,
        )
        session.add(requirement)
        session.flush()

        courses = [_course(session, source) for _ in range(COURSES_PER_PROGRAM)]
        for course in courses:
            session.add(RequirementCourseOption(
                requirement_id=requirement.id, course_id=course.id,
                source_id=source.id,
            ))
        session.flush()

        fixture = ProgramFixture(
            version_id=version.id, program_id=program.id,
            requirement_id=requirement.id,
            spare_course_ids=[c.id for c in courses[3:]],
        )
        for _ in range(STUDENTS_PER_PROGRAM):
            account = UserAccount(identity_provider="oidc",
                                  external_subject=f"bench-{uuid.uuid4()}")
            session.add(account)
            session.flush()
            student = Student(external_ref=f"bench-{uuid.uuid4()}",
                              catalog_year=version.catalog_year,
                              program_version_id=version.id,
                              user_id=account.id)
            session.add(student)
            session.flush()
            for course in courses[:3]:
                session.add(StudentCourse(
                    student_id=student.id, course_id=course.id,
                    term_code="20269", status="completed", grade="A",
                    credits_earned=decimal.Decimal("4.0"),
                ))
            fixture.student_ids.append(student.id)
        session.flush()
        fixtures.append(fixture)
    session.commit()
    return fixtures


# --------------------------------------------------------------------------
# the workload
# --------------------------------------------------------------------------


@dataclass
class Sample:
    scenario: str
    milliseconds: float
    from_cache: bool
    #: What the cache key said had moved, taken from the production stale
    #: counters rather than inferred. "" means a COLD miss - no row at all -
    #: which is not an invalidation and must not be counted as one.
    stale_cause: str = ""
    #: Only meaningful for a rules-only stale miss: did this student's OWN
    #: program rules change?
    own_rules_changed: bool | None = None

    @property
    def is_collateral(self) -> bool:
        """A miss a per-program rules version could have avoided.

        Three conditions, all necessary:

          * it was a STALE miss, not a cold one - a cold cache is not an
            invalidation and no versioning scheme avoids it;
          * the ONLY component that moved was the rules token - if the
            student's own coursework or the engine changed, the
            recomputation was required regardless;
          * the changed rules belonged to a DIFFERENT program version.

        Leaving any of the three out inflates the number, which is exactly
        what the first run of this benchmark did.
        """
        return (
            not self.from_cache
            and self.stale_cause == "rules"
            and self.own_rules_changed is False
        )


class Workload:
    def __init__(self, sessionmaker_, fixtures: list[ProgramFixture]) -> None:
        self.SM = sessionmaker_
        self.fixtures = fixtures
        self.samples: list[Sample] = []
        #: per-program rule fingerprints as last observed, so the benchmark
        #: can tell a legitimate invalidation from collateral damage.
        self._fingerprints: dict[uuid.UUID, str] = {}

    def _snapshot_fingerprints(self) -> None:
        with self.SM() as session:
            for fixture in self.fixtures:
                self._fingerprints[fixture.version_id] = rules_fingerprint(
                    session, fixture.version_id
                )

    def audit_everyone(self, scenario: str) -> None:
        """One audit per student, recording latency and invalidation cause.

        The cause is read from the production stale counters as a delta
        around each call, rather than guessed from the scenario name. That
        keeps the benchmark honest about cold misses, which look like
        invalidations from the outside and are not.
        """
        metrics = get_metrics()
        with self.SM() as session:
            for fixture in self.fixtures:
                current = rules_fingerprint(session, fixture.version_id)
                previous = self._fingerprints.get(fixture.version_id)
                # None means "first observation" - not "unchanged".
                own_rules_changed = (
                    None if previous is None else current != previous
                )

                for student_id in fixture.student_ids:
                    student = session.get(Student, student_id)
                    before = {
                        name: metrics.counter(name)
                        for name in (AUDIT_CACHE_STALE_ACADEMIC,
                                     AUDIT_CACHE_STALE_RULES,
                                     AUDIT_CACHE_STALE_ENGINE)
                    }
                    started = time.perf_counter()
                    _, from_cache = audit_with_cache(session, student)
                    elapsed = (time.perf_counter() - started) * 1000

                    moved = [
                        short
                        for name, short in (
                            (AUDIT_CACHE_STALE_ACADEMIC, "academic"),
                            (AUDIT_CACHE_STALE_RULES, "rules"),
                            (AUDIT_CACHE_STALE_ENGINE, "engine"),
                        )
                        if metrics.counter(name) > before[name]
                    ]
                    self.samples.append(Sample(
                        scenario=scenario,
                        milliseconds=elapsed,
                        from_cache=from_cache,
                        stale_cause="+".join(moved),
                        own_rules_changed=own_rules_changed,
                    ))
        self._snapshot_fingerprints()

    # --- mutations -------------------------------------------------------

    def change_one_students_record(self) -> None:
        """An academic change affecting exactly one student."""
        fixture = self.fixtures[0]
        with self.SM() as session:
            student_id = fixture.student_ids[0]
            course_id = fixture.spare_course_ids[0]
            session.add(StudentCourse(
                student_id=student_id, course_id=course_id,
                term_code="20271", status="completed", grade="B",
                credits_earned=decimal.Decimal("3.0"),
            ))
            session.commit()

    def change_one_programs_rules(self) -> None:
        """A recuration affecting exactly ONE program's requirements.

        This is the measurement that matters: a global version invalidates
        every student in every program, while only this program's students
        genuinely need recomputing.
        """
        fixture = self.fixtures[0]
        with self.SM() as session:
            requirement = session.get(Requirement, fixture.requirement_id)
            requirement.min_count = (requirement.min_count or 2) + 1
            session.commit()

    def change_one_programs_metadata(self) -> None:
        """Program.name reaches DegreeAuditResult - the Phase 5.8 fix.

        Targets a DIFFERENT program from the rule change where one exists, so
        the two mutations are independent. With a single program - which is
        CoursePilot's actual state - they necessarily coincide, and the
        collateral count correctly falls to zero.
        """
        fixture = self.fixtures[1 if len(self.fixtures) > 1 else 0]
        with self.SM() as session:
            program = session.get(Program, fixture.program_id)
            program.name = f"Renamed {uuid.uuid4().hex[:6]}"
            session.commit()

    def change_engine_version(self, monkey) -> None:
        monkey()


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(round(fraction * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[index]


def report(workload: Workload) -> dict:
    samples = workload.samples
    hits = [s for s in samples if s.from_cache]
    misses = [s for s in samples if not s.from_cache]
    all_ms = [s.milliseconds for s in samples]

    print("\n" + "=" * 74)
    print("CACHE EFFECTIVENESS")
    print("=" * 74)
    print(f"  total audits        {len(samples)}")
    print(f"  cache hits          {len(hits)}")
    print(f"  cache misses        {len(misses)}")
    if samples:
        print(f"  hit rate            {len(hits)/len(samples):.1%}")
        print(f"  miss rate           {len(misses)/len(samples):.1%}")
    if hits:
        warm = [s.milliseconds for s in hits]
        print(f"\n  warm (hit)   mean {statistics.mean(warm):7.2f}  "
              f"p50 {percentile(warm, .50):7.2f}  p95 {percentile(warm, .95):7.2f}")
    if misses:
        cold = [s.milliseconds for s in misses]
        print(f"  cold (miss)  mean {statistics.mean(cold):7.2f}  "
              f"p50 {percentile(cold, .50):7.2f}  p95 {percentile(cold, .95):7.2f}")
    if all_ms:
        print(f"  all          mean {statistics.mean(all_ms):7.2f}  "
              f"p50 {percentile(all_ms, .50):7.2f}  p95 {percentile(all_ms, .95):7.2f}")

    print("\n" + "=" * 74)
    print("PER-SCENARIO")
    print("=" * 74)
    scenarios: dict[str, list[Sample]] = {}
    for sample in samples:
        scenarios.setdefault(sample.scenario, []).append(sample)
    for name, group in scenarios.items():
        group_hits = sum(1 for s in group if s.from_cache)
        ms = [s.milliseconds for s in group]
        print(f"  {name:<34} n={len(group):<4} hits={group_hits:<4} "
              f"misses={len(group)-group_hits:<4} mean {statistics.mean(ms):7.2f} ms")

    print("\n" + "=" * 74)
    print("INVALIDATION ANALYSIS  (the Phase 5.9 question)")
    print("=" * 74)
    metrics = get_metrics()
    counters = metrics.snapshot()["counters"]
    for name in (AUDIT_CACHE_HITS, AUDIT_CACHE_MISSES,
                 AUDIT_CACHE_STALE_ACADEMIC, AUDIT_CACHE_STALE_RULES,
                 AUDIT_CACHE_STALE_ENGINE, AUDIT_CACHE_STALE_RULES_ONLY):
        print(f"  {name:<40} {counters.get(name, 0)}")

    # Break the misses down by what a per-program version could have done
    # about them. Only one bucket is avoidable.
    cold = [s for s in misses if not s.stale_cause]
    rules_only = [s for s in misses if s.stale_cause == "rules"]
    other_stale = [
        s for s in misses if s.stale_cause and s.stale_cause != "rules"
    ]
    necessary = [s for s in rules_only if s.own_rules_changed]
    collateral = [s for s in rules_only if s.own_rules_changed is False]

    print()
    print(f"  misses, by what caused them")
    print(f"    cold (no row yet)                      {len(cold):>4}"
          f"   unavoidable - not an invalidation")
    print(f"    academic / engine / combined           {len(other_stale):>4}"
          f"   required regardless of versioning")
    print(f"    rules only                             {len(rules_only):>4}")
    print(f"      ... own program's rules changed      {len(necessary):>4}"
          f"   NECESSARY under any design")
    print(f"      ... a different program's rules      {len(collateral):>4}"
          f"   COLLATERAL - what per-program would avoid")

    wasted_ms = sum(s.milliseconds for s in collateral)
    saveable = wasted_ms - len(collateral) * (
        statistics.mean([s.milliseconds for s in hits]) if hits else 0.0
    )
    print(f"\n  wall time on collateral recomputation        {wasted_ms:8.2f} ms")
    if all_ms:
        print(f"  as a share of all audit time                 "
              f"{100*wasted_ms/sum(all_ms):5.1f}%")
    print(f"  net saving if those had been hits instead    {saveable:8.2f} ms"
          f"   ({100*saveable/sum(all_ms):.1f}% of total)" if all_ms else "")

    return {
        "total": len(samples),
        "hits": len(hits),
        "misses": len(misses),
        "cold": len(cold),
        "other_stale": len(other_stale),
        "rules_only": len(rules_only),
        "necessary": len(necessary),
        "collateral": len(collateral),
        "wasted_ms": wasted_ms,
        "saveable_ms": saveable,
        "counters": counters,
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> int:
    url = os.environ.get("DATABASE_URL_SYNC")
    if not url or "coursepilot" not in url:
        print("Set DATABASE_URL_SYNC to a THROWAWAY copy of the dev database.",
              file=sys.stderr)
        return 2
    if url.rstrip("/").endswith("/coursepilot"):
        print("Refusing to run against the source development database.\n"
              "Make a copy first (CREATE DATABASE ... TEMPLATE coursepilot).",
              file=sys.stderr)
        return 2

    engine = create_engine(url, poolclass=NullPool)
    SM = sessionmaker(engine, expire_on_commit=False)
    try:
        print(f"building {PROGRAMS} programs x {STUDENTS_PER_PROGRAM} students ...")
        with SM() as session:
            fixtures = build(session)

        get_metrics().reset()
        workload = Workload(SM, fixtures)

        # 6. cold start - nothing cached
        workload.audit_everyone("1 cold start")
        # 1. repeated reads, no changes  (7. warm)
        workload.audit_everyone("2 warm, no changes")
        workload.audit_everyone("3 warm, no changes again")
        # 2. an academic change for ONE student
        workload.change_one_students_record()
        workload.audit_everyone("4 after one academic change")
        # 3. a rule change in ONE program
        workload.change_one_programs_rules()
        workload.audit_everyone("5 after one program's rule change")
        workload.audit_everyone("6 warm again")
        # 4. program metadata change in ONE program
        workload.change_one_programs_metadata()
        workload.audit_everyone("7 after one program rename")
        workload.audit_everyone("8 warm again")

        # 5. engine version change - legitimately global
        import app.services.audit.cache as cache_module

        original = cache_module.AUDIT_ENGINE_VERSION
        cache_module.AUDIT_ENGINE_VERSION = original + "-bench"
        try:
            workload.audit_everyone("9 after engine version change")
            workload.audit_everyone("10 warm again")
        finally:
            cache_module.AUDIT_ENGINE_VERSION = original

        report(workload)
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
