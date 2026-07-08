"""Integration tests for laptop endpoints (unscored category)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.laptop_fixtures import ensure_laptop_fixtures


def test_list_laptops(client: TestClient) -> None:
    ensure_laptop_fixtures()
    body = client.get("/v1/laptops").json()
    assert body["count"] >= 1
    slugs = {r["slug"] for r in body["results"]}
    assert "lenovo-legion-pro-5-test" in slugs


def test_laptop_detail_embeds_cpu_and_gpu(client: TestClient) -> None:
    ensure_laptop_fixtures()
    body = client.get("/v1/laptops/lenovo-legion-pro-5-test").json()
    assert body["slug"] == "lenovo-legion-pro-5-test"
    assert body["base_model_slug"] == "legion-pro-5"
    assert body["brand"]["slug"] == "lenovo"
    assert body["cpu"]["slug"] == "test-core-ultra-7-255hx"
    assert body["gpu"]["slug"] == "test-rtx-5060-laptop"
    assert body["cpu_name"] == "Intel Core Ultra 7 255HX"
    assert body["ram_gb"] == 32
    assert body["device_category"] == "Gaming"
    # Laptops are unscored — no score field is present.
    assert "score" not in body


def test_laptop_filter_by_brand(client: TestClient) -> None:
    ensure_laptop_fixtures()
    body = client.get("/v1/laptops?brand=lenovo").json()
    assert body["count"] >= 1
    # Unknown brand yields an empty page, not an error.
    empty = client.get("/v1/laptops?brand=does-not-exist").json()
    assert empty["count"] == 0


def test_laptop_not_found(client: TestClient) -> None:
    ensure_laptop_fixtures()
    response = client.get("/v1/laptops/nonexistent-laptop")
    assert response.status_code == 404
