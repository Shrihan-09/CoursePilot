"""UI polish B2: the published registration schedule the Home "Next up" card reads."""

from __future__ import annotations

from httpx import AsyncClient


async def test_spring_2027_schedule_is_served_with_its_source(client: AsyncClient) -> None:
    response = await client.get("/api/v1/registration-schedules/20271")
    assert response.status_code == 200
    body = response.json()
    assert body["term_label"] == "Spring 2027"
    assert [t["min_degree_credits"] for t in body["tiers"]] == [105, 90, 75, 60, 40, 25, 1, 0]
    assert body["tiers"][0]["opens_at"].startswith("2026-11-09T09:00:00")
    assert body["all_students_opens_at"].startswith("2026-11-19T09:00:00")
    assert body["closes_at"].startswith("2026-11-20T02:00:00")
    assert body["nightly_closure"] == {"start": "02:00", "end": "06:00"}
    assert body["source_url"].startswith("https://nbregistrar.rutgers.edu/")


async def test_a_term_without_a_schedule_is_404_not_a_guess(client: AsyncClient) -> None:
    response = await client.get("/api/v1/registration-schedules/20279")
    assert response.status_code == 404
