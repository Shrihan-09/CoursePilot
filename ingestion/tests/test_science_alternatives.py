"""Real public Rutgers fixture, with a fabricated student."""
import json

import pytest
from app.domain.planning import PlanningConstraints

from coursepilot_ingestion.loaders.requirements import RequirementLoader
from coursepilot_ingestion.pipelines.courses import CourseIngestionPipeline
from coursepilot_ingestion.pipelines.sections import SectionIngestionPipeline
from coursepilot_ingestion.sources.soc import SocQuery

from .conftest import FIXTURE_DIR
from .test_requirement_aware_recommendations import (  # noqa: F401
    CS_BS,
    _plan,
    _student,
    world,
)


@pytest.fixture
def science_world(world, tmp_path):  # noqa: F811
    for term, rows in json.loads((FIXTURE_DIR / "soc_science_sequences.json").read_bytes()).items():
        folder = tmp_path / ("science-" + term)
        folder.mkdir()
        (folder / f"soc_courses_{term}_NB.json").write_text(json.dumps(rows), encoding="utf-8")
        year, season = term.split("_")
        query = SocQuery(year=int(year), term=season, campus="NB")
        CourseIngestionPipeline(world, folder).run(query, coverage="partial")
        SectionIngestionPipeline(world, folder).run(query)
    RequirementLoader(world).load_file(CS_BS)
    world.commit()
    return world


def test_bs_science_commits_to_one_sequence(science_world):
    student = _student(science_world, degree="BS")
    plan = _plan(science_world, student, constraints=PlanningConstraints(
        max_credits_per_term=19, max_courses_per_term=6))
    choices = [c.model_dump() for c in plan.metadata.alternative_choices]
    science = next(c for c in choices if c["requirement_code"] == "CS_BS_SCIENCE")
    assert len(science["chosen"]) == 1
    paths = {r.requirement_code.rsplit("_", 1)[0]
             for t in plan.terms for pc in t.courses for r in pc.requirements
             if r.requirement_code.startswith(("CS_BS_CHEM_", "CS_BS_PHYS_"))}
    assert paths == {science["chosen"][0]["code"]}


@pytest.mark.parametrize("branch", ["CS_BS_CHEM_B", "CS_BS_PHYS_A", "CS_BS_PHYS_C"])
def test_explicit_chemistry_or_physics_selection_is_exposed(science_world, branch):
    plan = _plan(science_world, _student(science_world, degree="BS"),
        constraints=PlanningConstraints(max_credits_per_term=19, max_courses_per_term=6,
            alternative_selections={"CS_BS_SCIENCE": [branch]}))
    choice = plan.metadata.alternative_choices[0]
    assert choice.selected == [branch]
    assert choice.status == "student_selected"
    assert [c.code for c in choice.chosen] == [branch]
    assert _science_paths(plan) <= {branch}


def test_invalid_branch_is_rejected(science_world):
    from app.services.planning.engine import InvalidAlternativeSelection
    with pytest.raises(InvalidAlternativeSelection):
        _plan(science_world, _student(science_world, degree="BS"), constraints=PlanningConstraints(
            alternative_selections={"CS_BS_SCIENCE": ["MADE_UP"]}))



def _science_paths(plan):
    return {r.requirement_code.rsplit("_", 1)[0]
            for t in plan.terms for pc in t.courses for r in pc.requirements
            if r.requirement_code.startswith(("CS_BS_CHEM_", "CS_BS_PHYS_"))}


def test_an_impossible_explicit_choice_needs_reselection_and_nothing_else_is_planned(
        science_world):
    """Physics 123/124/227/229 has no offering in the loaded terms."""
    plan = _plan(science_world, _student(science_world, degree="BS"),
                 constraints=PlanningConstraints(max_credits_per_term=19, max_courses_per_term=6,
                     alternative_selections={"CS_BS_SCIENCE": ["CS_BS_PHYS_B"]}))
    choice = plan.metadata.alternative_choices[0]
    assert choice.status == "needs_reselection"
    assert [c.code for c in choice.chosen] == ["CS_BS_PHYS_B"]       # held, not switched
    assert choice.recommended and choice.recommended[0].code != "CS_BS_PHYS_B"
    assert _science_paths(plan) <= {"CS_BS_PHYS_B"}                  # no other branch
    issues = [i for i in plan.issues if i.code == "ALTERNATIVE_NEEDS_RESELECTION"]
    assert issues and issues[0].requirement_code == "CS_BS_SCIENCE"


def test_a_started_sequence_is_continued_on_real_data(science_world):
    """A fabricated student who passed Physics 193 continues 193/194."""
    from .test_requirement_aware_recommendations import HISTORY
    student = _student(science_world, history=[*HISTORY, ("01:750:193", "20261", "B")],
                       degree="BS")
    plan = _plan(science_world, student, constraints=PlanningConstraints(
        max_credits_per_term=19, max_courses_per_term=6))
    choice = plan.metadata.alternative_choices[0]
    assert choice.status == "planner_recommended"
    assert [c.code for c in choice.chosen] == ["CS_BS_PHYS_C"]
    assert "already started" in choice.recommendation_reason
    assert _science_paths(plan) <= {"CS_BS_PHYS_C"}
