"""Integration tests for monitor endpoints (unscored category)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.monitor_fixtures import ensure_monitor_fixtures


def test_list_monitors(client: TestClient) -> None:
    ensure_monitor_fixtures()
    body = client.get("/v1/monitors").json()
    assert body["count"] >= 1
    # Filter by the fixture's unique base model so it is not buried under real
    # monitor data in pagination.
    filtered = client.get("/v1/monitors?base_model_slug=lg-ultragear-27gp850").json()
    slugs = {r["slug"] for r in filtered["results"]}
    assert "lg-ultragear-27gp850-test" in slugs


def test_monitor_detail(client: TestClient) -> None:
    ensure_monitor_fixtures()
    body = client.get("/v1/monitors/lg-ultragear-27gp850-test").json()
    assert body["slug"] == "lg-ultragear-27gp850-test"
    assert body["brand"]["slug"] == "lg"
    assert body["size_inch"] == 27.0
    assert body["resolution"] == "2560x1440"
    assert body["refresh_hz"] == 165
    assert body["panel_type"] == "IPS"
    # Monitors are unscored — no score field.
    assert "score" not in body


def test_monitor_filter_by_brand_and_panel(client: TestClient) -> None:
    ensure_monitor_fixtures()
    assert client.get("/v1/monitors?brand=lg").json()["count"] >= 1
    assert client.get("/v1/monitors?panel_type=IPS").json()["count"] >= 1
    assert client.get("/v1/monitors?brand=does-not-exist").json()["count"] == 0


def test_monitor_not_found(client: TestClient) -> None:
    ensure_monitor_fixtures()
    assert client.get("/v1/monitors/nonexistent-monitor").status_code == 404
