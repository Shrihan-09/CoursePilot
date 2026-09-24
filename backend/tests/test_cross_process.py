"""Cross-process BM25 semantics, with real processes (Phase 5.12, Part 7).

Phase 5.11 documented "one index per process" and "workers converge via the
database counter". Those were design statements verified in one process.
Here they are verified with **real subprocesses**, because an in-process
thread shares the registry and therefore proves nothing about the property.

The claim under test:

    process A ingests -> the version changes
    process B requests -> detects it -> rebuilds -> serves fresh results

with no shared memory, no message bus, and no restart.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import decimal
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.models import Course, DataSource, Subject

requires_db = pytest.mark.db

BACKEND = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    engine = create_engine(_url(), poolclass=NullPool)
    try:
        with sessionmaker(engine)() as session:
            yield session
    finally:
        engine.dispose()


def _url() -> str:
    return os.environ.get(
        "DATABASE_URL_SYNC",
        "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test",
    )


def _env() -> dict:
    env = dict(os.environ)
    env["DATABASE_URL_SYNC"] = _url()
    env["COURSEPILOT_ENV"] = "ci"
    env["PYTHONPATH"] = str(BACKEND)
    return env


def _run_worker(*args: str, timeout: int = 180) -> dict:
    """Run one real worker process and return its JSON report."""
    proc = subprocess.run(
        [sys.executable, "-m", "tests.support.worker_probe", *args],
        cwd=str(BACKEND), env=_env(), capture_output=True, text=True,
        timeout=timeout,
    )
    assert proc.returncode == 0, f"worker failed: {proc.stderr[-2000:]}"
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.startswith("{")]
    assert lines, f"no JSON from worker: {proc.stdout[-500:]} {proc.stderr[-500:]}"
    return json.loads(lines[-1])


def _make_course(session, *, title):
    suffix = uuid.uuid4().hex[:8]
    source = DataSource(kind="manual_curation", url=f"synthetic://xproc/{suffix}",
                        content_hash=suffix, retrieved_at=dt.datetime.now(dt.UTC),
                        version=1)
    session.add(source)
    session.flush()
    subject = Subject(code=uuid.uuid4().hex[:6], offering_unit_code="01",
                      description="XProc Subject", source_id=source.id)
    session.add(subject)
    session.flush()
    number = uuid.uuid4().hex[:6]
    course = Course(
        offering_unit_code="01", subject_code=subject.code, course_number=number,
        supplement_code="", course_string=f"01:{subject.code}:{number}",
        title=title, credits=decimal.Decimal("4.0"),
        subject_id=subject.id, source_id=source.id,
    )
    session.add(course)
    session.flush()
    session.commit()
    return course


# ==========================================================================
# one index per process
# ==========================================================================


@requires_db
def test_two_processes_each_build_their_own_index() -> None:
    """The documented cost of the design, verified rather than asserted.

    There is no shared memory: each worker pays its own build. What they DO
    share is the conclusion - both derive the same version from the same
    database counter.
    """
    with _session() as session:
        _make_course(session, title="Cross Process Probe")

    first = _run_worker("build")
    second = _run_worker("build")

    assert first["pid"] != second["pid"], "expected two distinct processes"
    # Each process built exactly once, and reused thereafter.
    assert first["builds"] == 1 and second["builds"] == 1
    assert first["reuse"] == 1 and second["reuse"] == 1
    assert first["reused"] is True and second["reused"] is True
    # Independently derived, identical conclusions.
    assert first["token"] == second["token"]
    assert first["index_version"] == second["index_version"]
    assert first["documents"] == second["documents"]


@requires_db
def test_metrics_are_per_process_and_do_not_aggregate() -> None:
    """Phase 5.10 said metrics are per process. This is the proof.

    Each worker reports `builds == 1`. If metrics aggregated across
    processes the second would report 2 - so this both confirms the
    limitation and shows it is understood rather than accidental.
    """
    with _session() as session:
        _make_course(session, title="Metrics Isolation Probe")

    reports = [_run_worker("build") for _ in range(3)]

    assert [r["builds"] for r in reports] == [1, 1, 1]
    assert len({r["pid"] for r in reports}) == 3


# ==========================================================================
# ingestion in A, detection in B
# ==========================================================================


@requires_db
def test_a_catalog_change_in_one_process_is_detected_by_another() -> None:
    """**The cross-process property.**

    Worker B builds an index, the parent (standing in for an ingestion
    process) mutates the catalog, and B detects it on its next request -
    with no shared memory, no message bus and no restart.
    """
    marker = f"crossproc{uuid.uuid4().hex[:6]}"
    with _session() as session:
        course = _make_course(session, title="Before Cross Process Change")
        course_id, key = course.id, course.course_string

    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.support.worker_probe", "detect", marker],
        cwd=str(BACKEND), env=_env(), text=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        ready = json.loads(proc.stdout.readline())
        assert ready["ready"] is True

        # The "ingestion": a different process writes the catalog.
        with _session() as session:
            session.execute(text("UPDATE course SET title = :t WHERE id = :i"),
                            {"t": f"{marker} Renamed", "i": course_id})
            session.commit()

        proc.stdin.write("go\n")
        proc.stdin.flush()
        out, err = proc.communicate(timeout=180)
    finally:
        if proc.poll() is None:                    # pragma: no cover
            proc.kill()

    lines = [ln for ln in out.strip().splitlines() if ln.startswith("{")]
    report = json.loads(lines[-1])

    assert report["before_version"] != report["after_version"], (
        "the worker did not observe the version change"
    )
    assert report["rebuilt"] is True
    assert key not in report["before_hits"]
    assert key in report["after_hits"], (
        "the worker served a stale index after a catalog change in another process"
    )


@requires_db
def test_two_processes_rebuilding_simultaneously_both_stay_correct() -> None:
    """No coordination between processes - and none needed.

    Both build independently from the same committed data, so both publish
    an index with the same version and the same ranking. Nothing broken is
    published because nothing is shared.
    """
    with _session() as session:
        _make_course(session, title="Simultaneous Data Structures Probe")

    start_at = time.time() + 3.0
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "tests.support.worker_probe",
             "simultaneous", str(start_at)],
            cwd=str(BACKEND), env=_env(), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for _ in range(3)
    ]
    reports = []
    try:
        for proc in procs:
            out, err = proc.communicate(timeout=180)
            assert proc.returncode == 0, err[-2000:]
            lines = [ln for ln in out.strip().splitlines() if ln.startswith("{")]
            reports.append(json.loads(lines[-1]))
    finally:
        for proc in procs:
            if proc.poll() is None:                # pragma: no cover
                proc.kill()

    assert len({r["pid"] for r in reports}) == 3
    assert len({r["version"] for r in reports}) == 1, "versions diverged"
    assert len({r["documents"] for r in reports}) == 1
    assert len({tuple(r["ranking"]) for r in reports}) == 1, "rankings diverged"


@requires_db
def test_a_worker_started_after_a_change_sees_the_new_corpus_immediately() -> None:
    """A newly started worker has no cache, so it builds from current data.

    This is what makes a rolling restart safe, and what makes "restart is
    not required" a statement about convenience rather than correctness.
    """
    marker = f"newworker{uuid.uuid4().hex[:6]}"
    before = _run_worker("build")

    with _session() as session:
        course = _make_course(session, title=f"{marker} Fresh Course")
        key = course.course_string

    after = _run_worker("build")

    assert after["token"] != before["token"]
    assert after["documents"] == before["documents"] + 1
    assert key  # the course exists; the worker's corpus grew to include it
