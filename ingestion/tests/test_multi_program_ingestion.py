"""A second real program: Rutgers SAS Mathematics beside Computer Science (Phase 6.0).

Everything here is real Rutgers data:

  * course rows come from archived SOC payloads (`soc_cs_courses_sample.json`,
    `soc_math_courses_sample.json` - see scripts/make_math_fixture.py);
  * the Mathematics requirements come from the archived official catalog page,
    encoded in `data/programs/.../sas-640-ba-option-a.json` with the sentence behind each
    node;
  * SAS Core is the same curated source already used for CS, loaded into BOTH
    majors.

The point is not Mathematics. It is that the same loader, the same schema and
the same Degree Engine hold two structurally different majors side by side,
and evaluate one real transcript under each without any code knowing which
major it is looking at.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import pathlib
import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.models import (
    Program,
    ProgramRule,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
    School,
    Student,
    StudentCourse,
)
from app.services.audit.engine import DegreeAuditEngine
from coursepilot_ingestion.loaders.postgres import CourseLoader
from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.normalizers.soc import SocNormalizer
from coursepilot_ingestion.parsers.soc import SocParser
from coursepilot_ingestion.pipelines.core import CoreIngestionPipeline
from coursepilot_ingestion.schemas import IngestionStats
from sqlalchemy import func, select

from .conftest import CS_REQUIREMENTS, FIXTURE_TERM, MATH_REQUIREMENTS, SAS_CORE

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
MATH_SOC = FIXTURES / "soc_math_courses_sample.json"
CS_SOC = FIXTURES / "soc_cs_courses_sample.json"
CORE_DEFINITION = SAS_CORE
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MATH_ARCHIVE = REPO_ROOT / "data" / "raw" / "catalog" / "catalog_mathematics-640_2026_2027.html"
NUXT = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

MATH_TARGET = {"school_code": "SAS", "program_code": "640", "degree_type": "BA"}


def _load_courses(session, payload_paths):
    """Load real SOC records, deduplicated by course string across fixtures."""
    by_string: dict[str, dict] = {}
    for path in payload_paths:
        for record in json.loads(path.read_bytes()):
            by_string.setdefault(record["courseString"], record)
    raw = SocParser().parse(json.dumps(list(by_string.values())).encode()).courses
    normalizer = SocNormalizer(term_code=FIXTURE_TERM)
    loader = CourseLoader(session)
    source = loader.get_or_create_source(
        kind="rutgers_official_api",
        url="https://classes.rutgers.edu/soc/api/courses.json?year=2026&term=9&campus=NB",
        content_hash="multi-program-fixture",
        retrieved_at=datetime.now(UTC),
        term_code=FIXTURE_TERM,
        academic_year="2026",
        archive_path=str(MATH_SOC),
        record_count=len(raw),
    )
    loader.load([normalizer.normalize(r) for r in raw], source, IngestionStats())
    session.flush()
    return by_string


@pytest.fixture
def two_programs(session, tmp_path):
    """CS and Mathematics, each with SAS Core attached, in one database."""
    records = _load_courses(session, [CS_SOC, MATH_SOC])
    cs_stats = RequirementLoader(session).load_file(CS_REQUIREMENTS)
    math_stats = RequirementLoader(session).load_file(MATH_REQUIREMENTS)
    # Committed before Core: the Core pipeline owns its transaction and
    # rolls back on commit=False, which would discard both majors with it.
    session.commit()

    archive = tmp_path / "soc_combined.json"
    archive.write_text(json.dumps(list(records.values())), encoding="utf-8")
    pipeline = CoreIngestionPipeline(session)
    pipeline.run(CORE_DEFINITION, archive, observed_term_code=FIXTURE_TERM)
    pipeline.run(CORE_DEFINITION, archive, observed_term_code=FIXTURE_TERM,
                 target_program=MATH_TARGET)
    return {"cs": cs_stats, "math": math_stats}


def _version(session, code: str) -> ProgramVersion:
    return session.scalar(
        select(ProgramVersion).join(Program).where(Program.code == code)
    )


def _student(session, ref: str, record: list[tuple[str, str]]) -> Student:
    """A student ENROLLED in CS, with real courses and real grades."""
    from app.models import Course

    cs = _version(session, "198")
    student = Student(external_ref=ref, catalog_year=cs.catalog_year,
                      program_version_id=cs.id)
    session.add(student)
    session.flush()
    for course_string, grade in record:
        course = session.scalar(select(Course).where(
            Course.course_string == course_string, Course.supplement_code == ""))
        assert course is not None, f"fixture is missing {course_string}"
        session.add(StudentCourse(student_id=student.id, course_id=course.id,
                                  term_code="20259", status="completed", grade=grade,
                                  credits_earned=course.credits))
    session.commit()
    return student


def _applied(result, system: str = "major") -> dict[str, set[str]]:
    """Allocations within one requirement system.

    Scoped because SAS Core is attached to both majors under
    share_across_systems: 01:640:151 also fills CORE_QFR (it is certified for
    quantitative reasoning) in BOTH programs, which is correct and is not
    what these assertions are about.
    """
    out: dict[str, set[str]] = {}
    for allocation in result.allocation:
        if allocation.requirement_system == system:
            out.setdefault(allocation.course.course_string, set()).add(
                allocation.requirement_code)
    return out


def _find(result, code):
    def walk(nodes):
        for n in nodes:
            if n.requirement_code == code:
                return n
            hit = walk(n.children)
            if hit:
                return hit
        return None

    return walk(result.requirements)


# ==========================================================================
# source authority
# ==========================================================================


def _archive_text() -> str:
    if not MATH_ARCHIVE.exists():
        pytest.skip(f"catalog archive missing: {MATH_ARCHIVE}")
    page = MATH_ARCHIVE.read_text(encoding="utf-8")
    flat = json.loads(NUXT.search(page).group(1))
    text = " ".join(
        html_lib.unescape(re.sub(r"<[^>]+>", " ", s)) for s in flat if isinstance(s, str)
    )
    return re.sub(r"\s+", " ", text)


def test_every_math_source_sentence_is_verbatim_in_the_archived_page() -> None:
    """The definition may only QUOTE Rutgers. `...` marks an elision."""
    text = _archive_text()
    definition = json.loads(MATH_REQUIREMENTS.read_bytes())
    prose = [("version", definition["program_version"]["source_prose"])]
    prose += [(r["code"], r["source_prose"]) for r in definition["requirements"]]
    prose += [(r["code"], r["source_prose"]) for r in definition["program_rules"]]

    missing = [
        (code, fragment[:80])
        for code, sentence in prose
        for fragment in (f.strip(" .") for f in sentence.split("..."))
        if fragment and re.sub(r"\s+", " ", fragment) not in text
    ]
    assert missing == [], missing


def test_the_math_archive_is_the_page_the_definition_cites() -> None:
    if not MATH_ARCHIVE.exists():
        pytest.skip(f"catalog archive missing: {MATH_ARCHIVE}")
    digest = hashlib.sha256(MATH_ARCHIVE.read_text(encoding="utf-8").encode()).hexdigest()
    readme = " ".join(json.loads(MATH_REQUIREMENTS.read_bytes())["_README"])
    assert digest[:16] in readme


def test_an_ai_encoded_definition_is_not_marked_human_curated() -> None:
    """Source authority: a model's reading of prose is never authoritative."""
    definition = json.loads(MATH_REQUIREMENTS.read_bytes())
    assert definition["source"]["curation_status"] == "unverified"
    assert definition["source"]["kind"] == "rutgers_official_catalog"
    assert "mathematics-640" in definition["source"]["url"]


# ==========================================================================
# loading
# ==========================================================================


def test_math_loads_beside_cs_without_collision(session, two_programs) -> None:
    """Part 3: two programs, one school, overlapping requirement codes."""
    assert two_programs["math"].unresolved_courses == []
    assert two_programs["math"].programs_inserted == 1
    assert two_programs["math"].schools_inserted == 0          # SAS already existed

    assert session.scalar(select(func.count()).select_from(School)) == 1
    programs = {(p.code, p.name) for p in session.scalars(select(Program))}
    assert programs == {("198", "Computer Science"), ("640", "Mathematics")}

    # Both versions contain a requirement coded MATH_151 - legal, because a
    # code is unique only within its version.
    owners = session.scalars(
        select(Requirement.program_version_id).where(Requirement.code == "MATH_151")
    ).all()
    assert len(set(owners)) == 2


def test_math_provenance_and_curation_status(session, two_programs) -> None:
    math = _version(session, "640")
    assert math.catalog_year == "2026-2027"
    assert math.curation_status == "unverified"
    assert "mathematics-640" in math.source_url
    statuses = set(session.scalars(select(Requirement.curation_status).where(
        Requirement.program_version_id == math.id,
        Requirement.requirement_system == "major")))
    assert statuses == {"unverified"}
    # Core joined Mathematics with ITS OWN, human-curated status intact.
    core = set(session.scalars(select(Requirement.curation_status).where(
        Requirement.program_version_id == math.id,
        Requirement.requirement_system == "core")))
    assert core == {"curated_from_prose"}
    rule = session.scalar(select(ProgramRule).where(ProgramRule.code == "MATH_RESIDENCY"))
    assert rule.is_evaluable is False and rule.not_evaluable_reason


def test_the_excluded_seminar_is_not_an_upper_level_option(session, two_programs) -> None:
    upper = session.scalar(select(Requirement).where(Requirement.code == "MATH_UPPER"))
    options = session.execute(
        select(RequirementCourseOption.category, func.count())
        .where(RequirementCourseOption.requirement_id == upper.id)
        .group_by(RequirementCourseOption.category)
    ).all()
    from app.models import Course

    eligible = set(session.scalars(
        select(Course.course_string)
        .join(RequirementCourseOption, RequirementCourseOption.course_id == Course.id)
        .where(RequirementCourseOption.requirement_id == upper.id)))
    assert "01:640:491" not in eligible and "01:640:492" not in eligible
    assert {"01:640:311", "01:640:350", "01:640:361"} <= eligible
    assert dict(options)["ANALYSIS"] == 4 and dict(options)["ALGEBRA"] == 4


def test_reloading_math_is_idempotent(session, two_programs) -> None:
    before = session.scalar(select(func.count()).select_from(RequirementCourseOption))
    again = RequirementLoader(session).load_file(MATH_REQUIREMENTS)
    assert (again.requirements_inserted, again.eligibility_inserted,
            again.rules_inserted, again.versions_inserted) == (0, 0, 0, 0)
    assert session.scalar(select(func.count()).select_from(RequirementCourseOption)) == before


def test_a_recurated_requirement_is_refreshed_on_reload(session, two_programs) -> None:
    """Found in Phase 6.0 on the development database.

    Reloading updated only a requirement's name, type, system and order, so a
    recuration that changed a COUNT or the quoted PROSE loaded "successfully"
    and changed nothing - leaving a row that no longer matched its cited
    source.
    """
    definition = json.loads(MATH_REQUIREMENTS.read_bytes())
    upper = next(r for r in definition["requirements"] if r["code"] == "MATH_UPPER")
    upper["min_count"] = 7
    upper["source_prose"] = "revised sentence"
    raw = json.dumps(definition).encode()
    stats = RequirementLoader(session).load(definition, raw)
    session.flush()

    row = session.scalar(select(Requirement).where(Requirement.code == "MATH_UPPER"))
    assert stats.requirements_updated == len(definition["requirements"])
    assert (row.min_count, row.source_prose) == (7, "revised sentence")


def test_a_recurated_core_requirement_is_refreshed_on_reload(session, two_programs, tmp_path) -> None:
    """The same defect in the Core loader, which is how the development
    database came to hold two different texts for CS's and Math's CORE_AH."""
    definition = json.loads(CORE_DEFINITION.read_bytes())
    node = next(r for r in definition["requirements"] if r["code"] == "CORE_AH")
    node["source_prose"] = "revised AH sentence"
    path = tmp_path / "core.json"
    path.write_text(json.dumps(definition), encoding="utf-8")
    archive = tmp_path / "soc.json"
    archive.write_text("[]", encoding="utf-8")

    CoreIngestionPipeline(session).run(path, archive)                     # CS, default
    rows = session.scalars(select(Requirement).where(Requirement.code == "CORE_AH")).all()
    by_program = {session.get(ProgramVersion, r.program_version_id).program.code:
                  r.source_prose for r in rows}
    assert by_program["198"] == "revised AH sentence"
    assert by_program["640"] != "revised AH sentence"                     # untouched


def test_an_unknown_course_query_key_is_refused(session) -> None:
    with pytest.raises(ValueError, match="unknown eligible_course_query"):
        RequirementLoader(session)._query_courses({"subject_code": "640", "exclude": ["491"]})


def test_core_can_target_a_second_major(session, two_programs) -> None:
    """The same SAS Core source, attached to CS by default and to Math on request."""
    per_version = dict(session.execute(
        select(Program.code, func.count(Requirement.id))
        .join(ProgramVersion, ProgramVersion.program_id == Program.id)
        .join(Requirement, Requirement.program_version_id == ProgramVersion.id)
        .where(Requirement.requirement_system == "core")
        .group_by(Program.code)).all())
    assert per_version["198"] == per_version["640"] > 0
    assert _version(session, "640").sharing_policy == "share_across_systems"


def test_core_target_override_is_validated(session, tmp_path) -> None:
    archive = tmp_path / "soc.json"
    archive.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="target_program is missing"):
        CoreIngestionPipeline(session).run(CORE_DEFINITION, archive,
                                           target_program={"school_code": "SAS"})


# ==========================================================================
# one real transcript, two real programs, one engine
# ==========================================================================

CS_LEANING = [
    ("01:198:111", "A"), ("01:198:112", "B+"), ("01:198:205", "A"), ("01:198:211", "B"),
    ("01:640:151", "A"), ("01:640:152", "B"), ("01:640:250", "A"),
]
MATH_LEANING = [
    ("01:198:107", "A"),
    ("01:640:151", "A"), ("01:640:152", "A"), ("01:640:251", "B+"), ("01:640:250", "A"),
    ("01:640:252", "B"), ("01:640:311", "B"), ("01:640:350", "A"), ("01:640:351", "B+"),
    ("01:640:300", "A"), ("01:640:321", "B"), ("01:640:491", "A"),
]


def test_one_engine_evaluates_one_record_under_both_real_programs(session, two_programs) -> None:
    student = _student(session, "cs-leaning", CS_LEANING)
    engine = DegreeAuditEngine(session)
    cs = engine.audit(student)
    math = engine.audit(student, program_version=_version(session, "640"))

    assert (cs.program_name, math.program_name) == ("Computer Science", "Mathematics")
    cur, tgt = _applied(cs), _applied(math)

    # Case C (real): Calculus I is required by both - under two different
    # requirements that happen to share a code but come from different pages.
    assert cur["01:640:151"] == {"MATH_151"} and tgt["01:640:151"] == {"MATH_151"}
    assert _find(cs, "MATH_151").source_prose != _find(math, "MATH_151").source_prose
    # Case C (real): 01:198:111 is a CS core course and a Math computing option.
    assert cur["01:198:111"] == {"CS_111"} and tgt["01:198:111"] == {"MATH_COMPUTING"}
    # Case D (real): 01:198:112 means nothing to the Mathematics major.
    assert cur["01:198:112"] == {"CS_112"} and "01:198:112" not in tgt


def test_a_course_excluded_by_cs_counts_for_mathematics(session, two_programs) -> None:
    """Case E (real): CS denies 01:198:107 all credit; Mathematics requires it."""
    student = _student(session, "math-leaning", MATH_LEANING)
    engine = DegreeAuditEngine(session)
    cs = engine.audit(student)
    math = engine.audit(student, program_version=_version(session, "640"))

    assert "01:198:107" in {c.course_string for c in cs.excluded_courses}
    assert cs.credits_excluded == Decimal("4.0")
    assert "01:198:107" not in {c.course_string for c in math.excluded_courses}
    assert _applied(math)["01:198:107"] == {"MATH_COMPUTING"}

    upper = _find(math, "MATH_UPPER")
    # 311 (analysis) + 350, 351 (algebra) + 300, 321 = five of eight, with
    # both required categories covered. 491 is excluded by the prose.
    assert upper.satisfied_count == 5 and upper.needed_count == 8
    assert upper.distinct_categories == 2
    assert upper.status == "partially_satisfied"
    assert "01:640:491" in {c.course_string for c in math.unallocated_courses}
    # Every Mathematics foundation requirement is met by this record.
    assert _find(math, "MATH_FOUNDATION").status == "satisfied"


def test_an_empty_credit_requirement_reports_decimal_zero(session, two_programs) -> None:
    """Found in Phase 6.0 on the development record, in BOTH programs.

    SAS Core's CORE_NS is a credit requirement; with no eligible course the
    engine reported `satisfied_credits` as the int 0, so serializing ANY
    such audit made Pydantic warn - an error under this suite's policy - and
    clients received the number 0 where every other value is a decimal string.
    """
    student = _student(session, "no-science", CS_LEANING)
    for version in (None, _version(session, "640")):
        result = DegreeAuditEngine(session).audit(student, program_version=version)
        node = _find(result, "CORE_NS")
        assert node.requirement_type == "credits"
        assert isinstance(node.satisfied_credits, Decimal)
        result.model_dump_json()          # would raise under filterwarnings=error


def test_the_real_comparison_matches_only_same_source_requirements(session, two_programs) -> None:
    from app.services.scenarios import compare_audits

    student = _student(session, "cs-leaning-2", CS_LEANING)
    engine = DegreeAuditEngine(session)
    comparison = compare_audits(
        engine.audit(student),
        engine.audit(student, program_version=_version(session, "640")))

    # Exactly the SAS Core tree: the one requirement set loaded from the same
    # source into both majors. Nothing else is "the same requirement".
    core_codes = {r["code"] for r in json.loads(CORE_DEFINITION.read_bytes())["requirements"]}
    shared = {r.code for r in comparison.requirements_in_both}
    assert shared == core_codes
    # CS's MATH_151 and Mathematics' MATH_151 are different requirements.
    assert "MATH_151" not in shared
    only_current = {r.code for r in comparison.requirements_only_current}
    only_target = {r.code for r in comparison.requirements_only_target}
    assert "MATH_151" in only_current and "MATH_151" in only_target
    assert "01:198:112" in {c.course_string for c in comparison.courses_applied_only_current}
