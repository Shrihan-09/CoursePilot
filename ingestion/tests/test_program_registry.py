"""Program discovery registry, lifecycle and provenance (Phase 6.3).

Offline and deterministic: synthetic Coursedog pages (tests/catalog_pages.py)
for the mechanics; the real archived pages, when present locally, for the
claims made about the real Rutgers catalog.
"""

from __future__ import annotations

import copy
import json
import pathlib
import re
from datetime import UTC, datetime

import pytest
from app.models import (
    CatalogPage,
    Program,
    ProgramCandidate,
    ProgramReview,
    ProgramVersion,
    Requirement,
    RequirementCourseOption,
)
from app.services import program_lifecycle as lc
from app.services.eligibility import reconcile
from app.services.programs import SupportStatus, list_discovered, list_programs
from sqlalchemy import func, select

from coursepilot_ingestion.catalog_registry import (
    EXTRACTOR_VERSION,
    CatalogRegistry,
    candidates,
    navigation,
    page_prose,
    prose_sha256,
)
from coursepilot_ingestion.loaders.requirements import RequirementLoader, definition_sha256
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.program_registry import (
    ARCHIVE_DIR,
    archive_name,
    curated_files,
    link_definition,
    validate_and_mark,
    validate_definition,
)
from coursepilot_ingestion.sources.soc import SocQuery

from .catalog_pages import MATH_BLOCKS, SAS_NAV, page
from .conftest import CS_REQUIREMENTS, CURATED_DIR, FIXTURE_DIR, MATH_REQUIREMENTS

T0 = datetime(2026, 9, 30, 12, tzinfo=UTC)
T1 = datetime(2026, 10, 7, 12, tzinfo=UTC)
ROOT = pathlib.Path(__file__).resolve().parents[2]


def _cs_definition() -> dict:
    return json.loads(CS_REQUIREMENTS.read_bytes())


def _load(session, definition: dict) -> ProgramVersion:
    RequirementLoader(session).load(definition, json.dumps(definition).encode())
    session.flush()
    return session.scalar(
        select(ProgramVersion).join(Program).where(
            Program.code == definition["program"]["code"],
            Program.variant == definition["program"].get("variant", ""),
            ProgramVersion.catalog_year == definition["program_version"]["catalog_year"]))


def _validated(session, definition=None) -> ProgramVersion:
    version = _load(session, definition or _cs_definition())
    lc.check_source(version, "a" * 64, T0)
    lc.mark_validated(version, T0)
    return version


# ==========================================================================
# discovery
# ==========================================================================


def test_navigation_classifies_pages_without_guessing_programs() -> None:
    leaves = {leaf.url_path: leaf for leaf in navigation(page(SAS_NAV, []))}
    assert leaves["/schools/sas/program-listing/mathematics-640"].page_class == "subject_coded"
    assert leaves["/schools/sas/program-listing/mathematics-640"].subject_code == "640"
    assert leaves["/schools/sas/academic-integrity"].page_class == "other"


def test_discovery_is_idempotent_and_never_deletes(session) -> None:
    html = page(SAS_NAV, [])
    registry = CatalogRegistry(session)
    first = registry.discover(html, "2026-2027", T0)
    second = registry.discover(html, "2026-2027", T1)
    assert (first.pages_inserted, second.pages_inserted, second.pages_updated) == (3, 0, 0)

    shrunk = copy.deepcopy(SAS_NAV)
    shrunk["sas"]["Policies"] = []
    t2 = datetime(2026, 10, 14, 12, tzinfo=UTC)
    registry.discover(page(shrunk, []), "2026-2027", t2)
    rows = list(session.scalars(select(CatalogPage)))
    assert len(rows) == 3                                     # kept, not deleted

    def seen(r):
        return r.last_seen_at.replace(tzinfo=UTC)

    gone = next(r for r in rows if r.url_path.endswith("academic-integrity"))
    assert seen(gone) == T1                                   # when it was last seen
    assert all(seen(r) == t2 for r in rows if r is not gone)
    assert all(r.lifecycle == "discovered" for r in rows)


def test_discovery_creates_no_program(session) -> None:
    CatalogRegistry(session).discover(page(SAS_NAV, []), "2026-2027", T0)
    assert session.scalar(select(func.count()).select_from(Program)) == 0
    assert list_programs(session) == []


def test_a_page_is_not_a_program(session) -> None:
    """One page -> many candidate credentials; none of them is a program."""
    parsed = candidates(page(SAS_NAV, MATH_BLOCKS))
    kinds = {c.candidate_key: c.credential_type for c in parsed.candidates}
    assert kinds["major"] == "major"
    assert kinds["major/option-a"] == "option" and kinds["major/option-b"] == "option"
    assert "interdisciplinary_major" in kinds.values()
    assert "minor" in kinds.values() and "certificate" in kinds.values()
    option_a = next(c for c in parsed.candidates if c.candidate_key == "major/option-a")
    assert option_a.parent_candidate_key == "major" and option_a.curriculum_code == "640"
    # Admission is kept apart from degree completion, never a credential.
    assert parsed.admission_headings == ["Entry Requirements for the Major"]
    assert all("entry" not in c.heading.lower() for c in parsed.candidates)

    registry = CatalogRegistry(session)
    registry.discover(page(SAS_NAV, []), "2026-2027", T0)
    math = session.scalar(select(CatalogPage).where(CatalogPage.subject_code == "640"))
    stats = registry.record_fetch(math, page(SAS_NAV, MATH_BLOCKS), None, T0)
    assert stats.candidates_inserted == len(parsed.candidates)
    assert session.scalar(select(func.count()).select_from(Program)) == 0
    rows = list(session.scalars(select(ProgramCandidate)))
    assert all(r.program_version_id is None and r.extraction_method == EXTRACTOR_VERSION
               for r in rows)


def test_refetch_is_idempotent_and_reports_retired_candidates(session) -> None:
    registry = CatalogRegistry(session)
    registry.discover(page(SAS_NAV, []), "2026-2027", T0)
    math = session.scalar(select(CatalogPage).where(CatalogPage.subject_code == "640"))
    registry.record_fetch(math, page(SAS_NAV, MATH_BLOCKS), None, T0)
    again = registry.record_fetch(math, page(SAS_NAV, MATH_BLOCKS), None, T1)
    assert again.candidates_inserted == 0 and again.candidates_retired == []

    without_b = [MATH_BLOCKS[0].replace("<p><strong>Option B, Honors Mathematics</strong></p>", ""),
                 MATH_BLOCKS[1]]
    retired = registry.record_fetch(math, page(SAS_NAV, without_b), None, T1)
    assert retired.candidates_retired == ["major/option-b"]
    # Reported, kept: a curated version may still point at it.
    assert session.scalar(select(func.count()).select_from(ProgramCandidate).where(
        ProgramCandidate.candidate_key == "major/option-b")) == 1


def test_prose_hash_ignores_markup_and_build_churn() -> None:
    a = page(SAS_NAV, MATH_BLOCKS)
    b = page(SAS_NAV, [blk.replace("<p>", "<p class='x' data-v-1a2b>") for blk in MATH_BLOCKS])
    assert prose_sha256(a) == prose_sha256(b)
    c = page(SAS_NAV, [MATH_BLOCKS[0].replace("eight", "nine"), MATH_BLOCKS[1]])
    assert prose_sha256(a) != prose_sha256(c)


def test_catalog_years_are_isolated_even_with_a_stable_page_id(session) -> None:
    """Same Coursedog pageId in both years: two pages, two versions, and a
    change to one year's definition never touches the other's state."""
    registry = CatalogRegistry(session)
    registry.discover(page(SAS_NAV, []), "2025-2026", T0)
    registry.discover(page(SAS_NAV, []), "2026-2027", T0)
    pages = list(session.scalars(select(CatalogPage).where(CatalogPage.subject_code == "198")))
    assert len(pages) == 2 and len({p.source_page_id for p in pages}) == 1

    current = _cs_definition()
    older = copy.deepcopy(current)
    older["program_version"]["catalog_year"] = "2025-2026"
    older["source"]["catalog_year"] = "2025-2026"
    v26 = _validated(session, current)
    v25 = _load(session, older)
    assert v25.id != v26.id

    older["requirements"][-1]["min_count"] = 4                  # 2025-26 changes
    _load(session, older)
    assert v26.lifecycle_state == "validated"                   # 2026-27 untouched
    assert v26.definition_sha256 == definition_sha256(current)


# ==========================================================================
# lifecycle
# ==========================================================================


def test_parsed_cannot_be_published_or_reviewed(session) -> None:
    version = _load(session, _cs_definition())
    assert version.lifecycle_state == "parsed"
    with pytest.raises(lc.LifecycleError):
        lc.publish(session, version)
    with pytest.raises(lc.LifecycleError):
        lc.record_review(session, version, reviewer="Jordan Rivera", decision="approved")
    assert version.lifecycle_state == "parsed"


def test_publish_requires_an_approved_review_on_record(session) -> None:
    """A state forged outside the lifecycle (e.g. by SQL) still cannot publish."""
    version = _validated(session)
    version.lifecycle_state = "reviewed"            # forged: no review exists
    with pytest.raises(lc.LifecycleError, match="no approved human review"):
        lc.publish(session, version)
    assert version.lifecycle_state == "reviewed" and version.publication_basis is None


def test_publish_requires_the_reviewed_state_even_with_a_matching_review(session) -> None:
    version = _validated(session)
    lc.record_review(session, version, reviewer="Jordan Rivera", decision="approved")
    version.lifecycle_state = "validated"           # forged step back
    with pytest.raises(lc.LifecycleError, match="only a reviewed version"):
        lc.publish(session, version)


def test_publish_refuses_a_review_of_different_content(session) -> None:
    """Hashes edited behind the lifecycle's back: the review no longer covers them."""
    version = _validated(session)
    lc.record_review(session, version, reviewer="Jordan Rivera", decision="approved")
    version.definition_sha256 = "f" * 64
    with pytest.raises(lc.LifecycleError, match="does not cover"):
        lc.publish(session, version)
    version.definition_sha256 = session.scalar(select(ProgramReview.definition_sha256))
    version.source_prose_sha256 = "e" * 64
    with pytest.raises(lc.LifecycleError, match="does not cover"):
        lc.publish(session, version)


@pytest.mark.parametrize("reviewer", [
    "Claude", "claude-opus", "AI", "AI assistant", "Anthropic", "ChatGPT", "GPT-5", "LLM",
    "system", "System Validator", "bot", "automated check", "script", "pipeline", "CI",
    "unknown", "N/A", "", "  ", "ab", "CoursePilot", "test", "schema validator",
])
def test_machine_and_placeholder_reviewers_are_refused(session, reviewer) -> None:
    version = _validated(session)
    with pytest.raises(lc.LifecycleError):
        lc.record_review(session, version, reviewer=reviewer, decision="approved")
    assert version.lifecycle_state == "validated"
    assert session.scalar(select(func.count()).select_from(ProgramReview)) == 0


def test_validation_never_reaches_reviewed(session) -> None:
    version = _validated(session)
    lc.mark_validated(version, T1)                 # again: still only validated
    assert version.lifecycle_state == "validated"
    assert session.scalar(select(func.count()).select_from(ProgramReview)) == 0


def test_human_review_then_publish(session) -> None:
    version = _validated(session)
    review = lc.record_review(session, version, reviewer="Jordan Rivera",
                              decision="approved", notes="checked against archive", now=T1)
    assert version.lifecycle_state == "reviewed"
    assert (review.source_prose_sha256, review.definition_sha256) == (
        version.source_prose_sha256, version.definition_sha256)
    lc.publish(session, version)
    assert (version.lifecycle_state, version.publication_basis) == ("published", "human_review")


def test_changes_requested_does_not_advance(session) -> None:
    version = _validated(session)
    lc.record_review(session, version, reviewer="Jordan Rivera", decision="changes_requested")
    assert version.lifecycle_state == "validated"
    with pytest.raises(lc.LifecycleError):
        lc.publish(session, version)


def test_a_review_covers_one_definition_only(session) -> None:
    version = _validated(session)
    lc.record_review(session, version, reviewer="Jordan Rivera", decision="approved")
    changed = _cs_definition()
    changed["requirements"][-1]["min_count"] = 6
    _load(session, changed)                         # loader re-encodes after review
    assert version.lifecycle_state == "needs_rereview"
    with pytest.raises(lc.LifecycleError):
        lc.publish(session, version)


def test_source_change_downgrades_a_published_version(session) -> None:
    version = _validated(session)
    lc.record_review(session, version, reviewer="Jordan Rivera", decision="approved")
    lc.publish(session, version)
    assert lc.check_source(version, "a" * 64, T1) == "unchanged"
    assert version.lifecycle_state == "published"
    assert lc.check_source(version, "b" * 64, T1) == "changed"
    assert version.lifecycle_state == "needs_rereview"
    # The review that approved the OLD text is history, not deleted.
    assert session.scalar(select(ProgramReview.source_prose_sha256)) == "a" * 64


def test_needs_rereview_is_not_supported(session) -> None:
    version = _validated(session)
    lc.record_review(session, version, reviewer="Jordan Rivera", decision="approved")
    lc.publish(session, version)
    session.flush()
    assert list_programs(session)[0].versions[0].support_status is SupportStatus.SUPPORTED
    lc.check_source(version, "c" * 64, T1)
    session.flush()
    info = list_programs(session)[0].versions[0]
    assert (info.support_status, info.lifecycle_state) == (SupportStatus.PENDING_REVIEW,
                                                           "needs_rereview")


def test_documentation_edits_do_not_invalidate_a_review(session) -> None:
    definition = _cs_definition()
    version = _validated(session, definition)
    lc.record_review(session, version, reviewer="Jordan Rivera", decision="approved")
    edited = copy.deepcopy(definition)
    edited["_README"].append("clarified wording")
    edited["curation"]["curated_by"] = "someone else"
    assert definition_sha256(edited) == definition_sha256(definition)
    _load(session, edited)
    assert version.lifecycle_state == "reviewed"


def test_only_the_lifecycle_module_assigns_lifecycle_state() -> None:
    """Every `x.lifecycle_state = ...` in application code lives in one module."""
    offenders = []
    for base in (ROOT / "backend" / "app", ROOT / "ingestion" / "coursepilot_ingestion"):
        for path in base.rglob("*.py"):
            if path.name == "program_lifecycle.py" or "migrations" in path.parts:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"\.lifecycle_state\s*=[^=]", line):
                    offenders.append(f"{path.name}:{n}")
    assert offenders == []


# ==========================================================================
# provenance and validation
# ==========================================================================


def test_loader_records_provenance_without_claiming_review(session) -> None:
    definition = json.loads(MATH_REQUIREMENTS.read_bytes())
    version = _load(session, definition)
    assert version.program.variant == "option-a"
    assert "not a reviewer" in version.curated_by
    assert version.extractor_version == "manual-encoding/1"
    assert version.definition_sha256 == definition_sha256(definition)
    assert version.lifecycle_state == "parsed" and version.publication_basis is None
    assert session.scalar(select(func.count()).select_from(ProgramReview)) == 0


def test_link_sets_page_snapshot_candidate_and_baseline(session) -> None:
    definition = _cs_definition()
    html = page(SAS_NAV, ["<h3>Major Requirements</h3><p>CS prose</p>"])
    registry = CatalogRegistry(session)
    registry.discover(html, "2026-2027", T0)
    cs_page = session.scalar(select(CatalogPage).where(CatalogPage.subject_code == "198"))
    registry.record_fetch(cs_page, html, None, T0)
    cs_page.snapshot_id = _fake_snapshot(session)
    version = _load(session, definition)
    result = link_definition(session, definition, version, T0)
    assert (result.page_found, result.fetched, result.candidate_linked) == (True, True, True)
    assert result.source_check == "baseline"
    assert version.catalog_page_id == cs_page.id
    assert version.source_prose_sha256 == prose_sha256(html)
    assert list_discovered(session, "2026-2027")[0].page_title.startswith("Mathematics")
    assert all(d.url_path != cs_page.url_path or d.candidate_key != "major"
               for d in list_discovered(session))


def _fake_snapshot(session):
    from app.models import DataSource

    src = DataSource(kind="rutgers_official_catalog", url="synthetic://cs", content_hash="x" * 64,
                     retrieved_at=T0)
    session.add(src)
    session.flush()
    return src.id


def test_validator_rejects_quotes_the_page_does_not_contain() -> None:
    definition = _cs_definition()
    html = page(SAS_NAV, ["<p>" + definition["program_version"]["source_prose"] + "</p>"])
    report = validate_definition(definition, html)
    assert not report.ok
    assert all("quote not found" in e for e in report.errors)
    bad = copy.deepcopy(definition)
    bad["requirements"][-1]["course_categories"] = {"A_VERY_LONG_CATEGORY_NAME": ["01:198:336"]}
    assert any("exceeds" in e for e in validate_definition(bad, html).errors)


def test_validate_and_mark_reaches_validated_only(session) -> None:
    definition = _cs_definition()
    version = _load(session, definition)
    lc.check_source(version, "a" * 64, T0)
    every_quote = " ".join(r.get("source_prose") or "" for r in definition["requirements"])
    every_quote += " " + " ".join(r.get("source_prose") or "" for r in definition["program_rules"])
    every_quote += " " + definition["program_version"]["source_prose"]
    report = validate_and_mark(session, definition, page(SAS_NAV, ["<p>" + every_quote + "</p>"]))
    assert report.ok, report.errors
    assert version.lifecycle_state == "validated"


# ==========================================================================
# eligibility reconciliation: idempotency, transaction safety, genericity
# ==========================================================================


def _soc(session, tmp_path, fixture):
    query = SocQuery(year=2026, term="9", campus="NB")
    (tmp_path / f"soc_courses_{query.year}_{query.term}_{query.campus}.json").write_bytes(
        (FIXTURE_DIR / fixture).read_bytes())
    CourseIngestionPipeline(session, tmp_path).run(query)


def _options(session):
    return set(session.execute(select(RequirementCourseOption.requirement_id,
                                      RequirementCourseOption.course_id,
                                      RequirementCourseOption.category)).all())


def test_reconcile_is_idempotent(session, tmp_path) -> None:
    _soc(session, tmp_path, "soc_cs_courses_sample.json")
    _load(session, _cs_definition())
    before = _options(session)
    report = reconcile(session)
    assert (report.inserted, report.deleted) == (0, 0) and report.unchanged == len(before)
    assert _options(session) == before


def test_reconcile_failure_leaves_eligibility_untouched(session, tmp_path, monkeypatch) -> None:
    _soc(session, tmp_path, "soc_cs_courses_sample.json")
    definition = _cs_definition()
    _load(session, definition)
    session.commit()
    before = _options(session)

    electives = session.scalar(select(Requirement).where(Requirement.code == "CS_ELECTIVES"))
    electives.eligibility_rule = {"courses": ["01:198:111"]}       # would delete many rows
    calls = {"n": 0}
    real_delete = session.delete

    def failing_delete(obj):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("injected failure mid-reconciliation")
        return real_delete(obj)

    monkeypatch.setattr(session, "delete", failing_delete)
    with pytest.raises(RuntimeError):
        reconcile(session)
    monkeypatch.setattr(session, "delete", real_delete)
    session.rollback()
    assert _options(session) == before


def test_requirements_without_a_rule_are_never_touched(session, tmp_path) -> None:
    """SAS Core-style eligibility (from another source) survives reconciliation."""
    _soc(session, tmp_path, "soc_cs_courses_sample.json")
    version = _load(session, _cs_definition())
    other = session.scalar(select(Requirement).where(Requirement.code == "CS_111"))
    foreign = Requirement(program_version_id=version.id, code="CORE_X", name="core",
                          requirement_type="choose_n", min_count=1, source_id=other.source_id)
    session.add(foreign)
    session.flush()
    course_id = session.scalar(select(RequirementCourseOption.course_id).limit(1))
    session.add(RequirementCourseOption(requirement_id=foreign.id, course_id=course_id,
                                        category="AHp", source_id=other.source_id))
    session.flush()
    reconcile(session)
    assert session.scalar(select(func.count()).select_from(RequirementCourseOption).where(
        RequirementCourseOption.requirement_id == foreign.id)) == 1


# ==========================================================================
# the curated data itself
# ==========================================================================


def test_curated_definitions_live_outside_tests_and_are_honest() -> None:
    files = curated_files()
    assert len(files) >= 8
    assert all("tests" not in f.parts for f in files)
    for f in files:
        d = json.loads(f.read_bytes())
        curation = d.get("curation", {})
        assert curation.get("catalog_page", "").startswith("/schools/"), f.name
        assert "reviewed_by" not in json.dumps(d), f.name               # never self-reviewed
        if "AI" in curation.get("curated_by", ""):
            assert d["source"]["curation_status"] == "unverified", f.name
            assert "not a reviewer" in curation["curated_by"], f.name
    assert (CURATED_DIR / "sas-640-ba-option-a.json").exists()


def _archive(definition: dict) -> pathlib.Path:
    return ARCHIVE_DIR / archive_name(definition["program_version"]["catalog_year"],
                                      definition["curation"]["catalog_page"])


@pytest.mark.skipif(not (ARCHIVE_DIR / "catalog_mathematics-640_2026_2027.html").exists(),
                    reason="real catalog archives are local-only (data/raw is gitignored)")
def test_real_archives_quotes_and_candidates() -> None:
    math_html = (ARCHIVE_DIR / "catalog_mathematics-640_2026_2027.html").read_text(encoding="utf-8")
    keys = {c.candidate_key for c in candidates(math_html).candidates}
    assert {"major", "major/option-a", "major/option-b", "major/option-c"} <= keys
    assert len(navigation(math_html)) == 516
    assert "Option A" in page_prose(math_html)

    failures = {}
    for f in curated_files():
        d = json.loads(f.read_bytes())
        if _archive(d).exists():
            report = validate_definition(d, _archive(d).read_text(encoding="utf-8"))
            if not report.ok:
                failures[f.name] = report.errors
    # One known, reported defect: the CS elective quote omits a sentence
    # without marking the elision. Left for the human reviewer - editing it
    # would change the published CS definition.
    assert list(failures) == ["sas-198-ba.json"]
    assert len(failures["sas-198-ba.json"]) == 1 and "CS_ELECTIVES" in failures["sas-198-ba.json"][0]
