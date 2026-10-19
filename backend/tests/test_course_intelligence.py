"""Fabricated modality and valid-candidate ranking evidence; no real students."""

import itertools
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.course_ranking import CourseRankingEvidence
from app.domain.modality import Modality, ModalityPreferences
from app.domain.planning_profile import PlanningProfileInput
from app.services.planning.ranking import rank_key
from app.services.scheduling.meetings import normalize, time_verified
from app.services.scheduling.modality import aggregate, classify_meetings


def meeting(*, online=False, timed=True, room=True):
    return normalize(
        day="M" if timed else None,
        start="1000" if timed else None,
        end="1100" if timed else None,
        mode_code="x",
        mode_desc="ONLINE INSTRUCTION(INTERNET)" if online else "LEC",
        campus="ONL" if online else "BUS",
        building="ABC" if room else None,
        room="101" if room else None,
    )


def test_modality_uses_complete_explicit_evidence():
    asynchronous = classify_meetings([meeting(online=True, timed=False)], index="00001")
    synchronous = classify_meetings([meeting(online=True)], index="00002")
    classroom = classify_meetings([meeting()], index="00003")
    assert asynchronous.classification is Modality.ASYNCHRONOUS
    assert synchronous.classification is Modality.SYNCHRONOUS_ONLINE
    assert classroom.classification is Modality.IN_PERSON
    combined = aggregate([asynchronous, classroom])
    assert combined.classification is Modality.HYBRID
    assert combined.component_indexes == ["00001", "00003"]
    assert classify_meetings([meeting(room=False)]).classification is Modality.UNKNOWN
    assert classify_meetings([meeting(timed=False)]).classification is Modality.UNKNOWN
    assert (
        classify_meetings([meeting()], unstructured_meetings=True).classification
        is Modality.UNKNOWN
    )
    assert aggregate([classroom, classify_meetings([])]).classification is Modality.UNKNOWN


def test_no_meetings_are_neither_free_time_nor_known_modality():
    assert not time_verified([])
    assert not classify_meetings([]).complete
    assert classify_meetings([]).classification is Modality.UNKNOWN


def test_modality_proposal_contract_normalizes_without_claiming_application():
    profile = PlanningProfileInput.model_validate(
        {
            "schedule": {
                "modality": {"preferred": "asynchronous", "excluded": ["in_person", "in_person"]}
            }
        }
    )
    assert profile.schedule.modality.excluded == [Modality.IN_PERSON]
    assert profile.schedule.modality.application_state == "not_applied"
    assert (
        PlanningProfileInput.model_validate(profile.model_dump()).model_dump()
        == profile.model_dump()
    )
    for invalid in (
        {"preferred": "unknown"},
        {"preferred": "asynchronous", "excluded": ["asynchronous"]},
        {"application_state": "applied"},
        {"excluded": ["in_person", "asynchronous", "hybrid", "synchronous_online"]},
    ):
        with pytest.raises(ValidationError):
            ModalityPreferences.model_validate(invalid)


def test_valid_candidate_rank_is_order_independent_and_uses_coverage_before_code():
    evidence = {
        "01:999:100": CourseRankingEvidence(candidate_requirement_coverage=1),
        "01:999:900": CourseRankingEvidence(candidate_requirement_coverage=2),
    }
    for order in itertools.permutations(evidence):
        assert sorted(order, key=lambda c: rank_key(c, evidence[c])) == ["01:999:900", "01:999:100"]
    dependency = CourseRankingEvidence(required_dependency=True, scarcity=Decimal("99"))
    assert rank_key("01:999:999", dependency) < rank_key("01:999:100", evidence["01:999:100"])
    assert rank_key("01:999:100", evidence["01:999:100"]) < rank_key(
        "01:999:200", evidence["01:999:100"]
    )


def test_coverage_does_not_equate_legacy_publication_or_machine_validation_with_review():
    from types import SimpleNamespace as Row

    from app.services.program_coverage import coverage_report

    legacy = Row(
        id="legacy",
        catalog_year="2099-2100",
        lifecycle_state="published",
        validated_at=None,
        publication_basis="legacy_curated",
        definition_sha256="a",
        source_prose_sha256="s",
    )
    validated = Row(
        id="validated",
        catalog_year="2099-2100",
        lifecycle_state="validated",
        validated_at=True,
        publication_basis=None,
        definition_sha256="b",
        source_prose_sha256="s",
    )
    stale_review = Row(
        program_version_id="validated",
        decision="approved",
        definition_sha256="old",
        source_prose_sha256="s",
    )

    class PublicSession:
        rows = iter(
            [
                [
                    Row(catalog_year="2099-2100", snapshot_id=None),
                    Row(catalog_year="2099-2100", snapshot_id="snapshot"),
                ],
                ["2099-2100"],
                [stale_review],
                [legacy, validated],
            ]
        )

        def scalars(self, query):
            return next(self.rows)

    result = coverage_report(PublicSession())["catalog_years"]["2099-2100"]
    assert result["discovered_catalog_pages"] == 2
    assert result["fetched_catalog_pages"] == 1
    assert result["parsed_credential_candidates"] == 1
    assert result["currently_validated_programs"] == 1
    assert result["currently_human_reviewed_programs"] == 0
    assert result["published_programs"] == result["legacy_published_programs"] == 1
