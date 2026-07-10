"""Integration tests for software endpoints (unscored category)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.software_fixtures import ensure_software_fixtures


def test_list_software(client: TestClient) -> None:
    ensure_software_fixtures()
    body = client.get("/v1/software").json()
    assert body["count"] >= 1
    assert "results" in body


def test_software_detail(client: TestClient) -> None:
    ensure_software_fixtures()
    body = client.get("/v1/software/blender-test").json()
    assert body["slug"] == "blender-test"
    assert "Python" in body["programming_languages"]
    assert "Linux" in body["operating_systems"]
    # Software is unscored — no score field.
    assert "score" not in body


def test_software_not_found(client: TestClient) -> None:
    ensure_software_fixtures()
    assert client.get("/v1/software/nonexistent-software").status_code == 404
