"""Prerequisite lookup and evaluation against a student record (Phase 6.2).

PostgreSQL. Rows are built through the ORM with `parse()` producing the
stored interpretation, exactly as the ingestion loader does.
"""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import random
import uuid

import pytest
from sqlalchemy import event, select, text

from app.domain.prerequisites import PrereqStatus, parse, to_json
from app.models import (
    Course,
    CourseOffering,
    CoursePrerequisite,
    DataSource,
    PrerequisiteReference,
    Student,
    StudentCourse,
    Subject,
)

from .test_multi_program import _session, requires_db

SAT, UNSAT, UNK = PrereqStatus.SATISFIED, PrereqStatus.UNSATISFIED, PrereqStatus.UNKNOWN


def _source(session, term):
    s = DataSource(kind="rutgers_official_api", url="synthetic://prereq", term_code=term,
                   content_hash=uuid.uuid4().hex, retrieved_at=dt.datetime.now(dt.UTC))
    session.add(s)
    session.flush()
    return s


def _course(session, source):
    for _ in range(100):
        unit, subj, num = random.randint(90, 99), random.randint(100, 999), random.randint(100, 999)
        key = f"{unit}:{subj}:{num}"
        if not session.execute(text("SELECT 1 FROM course WHERE course_string=:k"), {"k": key}).first():
            break
    subject = session.scalar(select(Subject).where(Subject.code == str(subj),
                                                   Subject.offering_unit_code == str(unit)))
    if subject is None:
        subject = Subject(code=str(subj), offering_unit_code=str(unit), source_id=source.id)
        session.add(subject)
        session.flush()
    course = Course(offering_unit_code=str(unit), subject_code=str(subj), course_number=str(num),
                    supplement_code="", course_string=key, title=f"COURSE {key}",
                    credits=decimal.Decimal("3"), subject_id=subject.id, source_id=source.id)
    session.add(course)
    session.flush()
    return course


def _offer(session, course, term, source, raw=None, note=None, kinds=None):
    offering = CourseOffering(course_id=course.id, term_code=term, campus_code="NB",
                              source_id=source.id)
    session.add(offering)
    session.flush()
    if raw is None and note is None:
        return offering
    result = parse(raw) if raw else None
    session.add(CoursePrerequisite(
        offering_id=offering.id, course_id=course.id, term_code=term, raw_text=raw,
        raw_text_sha256=hashlib.sha256(raw.encode()).hexdigest() if raw else None,
        condition_note=note, condition_kinds=kinds,
        classification=result.classification.value if result else "condition_note_only",
        expression=to_json(result.expression) if result and result.expression else None,
        canonical_text=result.canonical_text if result else None,
        parser_version="1", source_id=source.id,
        references=[PrerequisiteReference(course_string=k) for k in (result.references if result else ())],
    ))
    session.flush()
    return offering


@pytest.fixture
def world():
    """Courses A, B, C and target courses with different prerequisites."""
    with _session() as session:
        s26, s25 = _source(session, "20269"), _source(session, "20259")
        a, b, c = (_course(session, s26) for _ in range(3))
        k = lambda x: x.course_string  # noqa: E731
        targets = {}
        for name, raw, note, kinds in (
            ("and", f"({k(a)} A )<em> AND </em>({k(b)} B )", None, None),
            ("or", f"({k(a)} A )<em> OR </em>({k(b)} B )", None, None),
            ("graded", f"({k(a)} A )", "Student needs C or better in all prerequisites.",
             "minimum_grade"),
            ("level", f"Any Course EQUAL or GREATER Than: ({k(a)} A )", None, None),
            ("none", None, None, None),
        ):
            target = _course(session, s26)
            _offer(session, target, "20269", s26, raw, note, kinds)
            targets[name] = target.course_string
        # A course whose prerequisite CHANGED between terms.
        changing = _course(session, s26)
        _offer(session, changing, "20259", s25, f"({k(a)} A )")
        _offer(session, changing, "20269", s26, f"({k(a)} A )<em> AND </em>({k(c)} C )")
        targets["changing"] = changing.course_string
        # A course that is not offered in 20269 at all.
        targets["not_offered"] = _course(session, s26).course_string

        # A minimal program of its own: prerequisites never consult it, but a
        # Student must be bound to some version.
        from app.models import Program, ProgramVersion, School

        tag = uuid.uuid4().hex[:8]
        school = School(code=f"Q{tag}", name="Prereq School", source_id=s26.id)
        session.add(school)
        session.flush()
        program = Program(school_id=school.id, code=tag, name="Prereq Program",
                          degree_type="BA", source_id=s26.id)
        session.add(program)
        session.flush()
        version = ProgramVersion(program_id=program.id, catalog_year="2026-2027",
                                 source_id=s26.id)
        session.add(version)
        session.flush()
        student = Student(external_ref=f"prq-{uuid.uuid4()}", catalog_year="2026-2027",
                          program_version_id=version.id)
        session.add(student)
        session.flush()
        session.commit()
        return {"student": student.id, "a": k(a), "b": k(b), "c": k(c),
                "ids": {"a": a.id, "b": b.id, "c": c.id}, "targets": targets}


def _record(session, world, *attempts):
    for key, term, status, grade in attempts:
        session.add(StudentCourse(student_id=world["student"], course_id=world["ids"][key],
                                  term_code=term, status=status, grade=grade,
                                  credits_earned=decimal.Decimal("3")))
    session.commit()


def _check(session, world, target, term="20269"):
    from app.services.prerequisites import check

    return check(session, session.get(Student, world["student"]), world["targets"][target], term)


@requires_db
def test_and_or_against_a_real_record(world) -> None:
    with _session() as session:
        _record(session, world, ("a", "20259", "completed", "A"))
        assert _check(session, world, "and").status is UNSAT
        assert _check(session, world, "or").status is SAT
        ev = _check(session, world, "and").evidence
        assert ev.satisfied_courses == [world["a"]] and ev.missing_courses == [world["b"]]


@requires_db
def test_a_grade_note_makes_a_met_expression_unknown(world) -> None:
    with _session() as session:
        _record(session, world, ("a", "20259", "completed", "B"))
        result = _check(session, world, "graded")
        assert result.status is UNK
        assert "unmodeled_condition:minimum_grade" in result.reasons
        assert result.condition_note.startswith("Student needs C")


@requires_db
def test_unsupported_rules_are_unknown_even_when_the_course_was_passed(world) -> None:
    with _session() as session:
        _record(session, world, ("a", "20259", "completed", "A"))
        assert _check(session, world, "level").status is UNK


@requires_db
def test_no_prerequisite_versus_no_data(world) -> None:
    with _session() as session:
        none = _check(session, world, "none")
        assert (none.status, none.has_prerequisite) == (SAT, False)
        missing = _check(session, world, "not_offered")
        assert missing.status is UNK and missing.reasons == ["no_offering_in_term"]


@requires_db
def test_the_prerequisite_of_the_requested_term_is_used(world) -> None:
    with _session() as session:
        _record(session, world, ("a", "20259", "completed", "A"))
        assert _check(session, world, "changing", "20259").status is SAT
        assert _check(session, world, "changing", "20269").status is UNSAT


@requires_db
def test_fail_then_pass_counts_as_passed_and_in_progress_is_unknown(world) -> None:
    with _session() as session:
        _record(session, world, ("a", "20259", "completed", "F"),
                ("a", "20261", "completed", "C"), ("b", "20269", "in_progress", None))
        assert _check(session, world, "or").status is SAT
        pending = _check(session, world, "and")
        assert pending.status is UNK and pending.evidence.pending_courses == [world["b"]]


@requires_db
def test_checking_many_candidates_is_not_n_plus_one(world) -> None:
    from app.services.prerequisites import check_many

    with _session() as session:
        student = session.get(Student, world["student"])
        statements: list[str] = []
        listener = lambda *args: statements.append(args[2])  # noqa: E731
        event.listen(session.get_bind(), "before_cursor_execute", listener)
        try:
            check_many(session, student, [world["targets"]["and"]], "20269")
            one = len(statements)
            statements.clear()
            results = check_many(session, student, list(world["targets"].values()), "20269")
            many = len(statements)
        finally:
            event.remove(session.get_bind(), "before_cursor_execute", listener)
    assert len(results) == len(world["targets"])
    assert one == many == 3
