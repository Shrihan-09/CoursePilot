"""Smoke tests: the app boots and serves without a database."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_is_ok_without_database(client: AsyncClient) -> None:
    # Liveness must not depend on Postgres, or a brief DB outage triggers
    # restart loops.
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_meta_reports_unimplemented_capabilities(client: AsyncClient) -> None:
    response = await client.get("/api/v1/meta")
    assert response.status_code == 200
    body = response.json()

    # Guards against shipping a UI for features that don't exist. As features
    # land, these flip to True here and in the endpoint together.
    assert body["capabilities"]["degree_audit"] is False
    assert body["capabilities"]["schedule_generation"] is False

    # No Rutgers data has been ingested, and we must not imply otherwise.
    assert body["loaded_terms"] == []
