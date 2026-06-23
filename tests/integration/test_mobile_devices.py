"""Integration tests for tablet, watch, and PDA endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.mobile_device_fixtures import ensure_mobile_device_fixtures


def test_list_mobile_device_categories(client: TestClient) -> None:
    ensure_mobile_device_fixtures()
    cases = [
        ("tablets", "ipad-pro-11-m4-wifi-8gb-256gb"),
        ("watches", "galaxy-watch-global-bluetooth-42mm"),
        ("pdas", "ipaq-h3600-base"),
    ]

    for resource, slug in cases:
        list_body = client.get(f"/v1/{resource}").json()
        detail_body = client.get(f"/v1/{resource}/{slug}").json()
        assert list_body["count"] >= 1
        assert detail_body["slug"] == slug


def test_mobile_device_detail_includes_variant_fields(client: TestClient) -> None:
    ensure_mobile_device_fixtures()
    body = client.get("/v1/tablets/ipad-pro-11-m4-wifi-8gb-256gb").json()
    assert body["slug"] == "ipad-pro-11-m4-wifi-8gb-256gb"
    assert body["base_model_slug"] == "ipad-pro-11-m4"
    assert body["brand"]["slug"] == "apple"
    assert body["variant"]["region"] == "global"
    assert body["variant"]["memory"] == {"ram_gb": 8, "storage_gb": 256}
    # `verified` is present and boolean; its value is data-driven (the verification
    # layer may promote this record), so don't assert a fixed value here.
    assert isinstance(body["verified"], bool)


def test_mobile_device_filters(client: TestClient) -> None:
    ensure_mobile_device_fixtures()
    brand_body = client.get("/v1/watches?brand=samsung").json()
    assert "galaxy-watch-global-bluetooth-42mm" in {
        item["slug"] for item in brand_body["results"]
    }

    base_body = client.get("/v1/tablets?base_model_slug=ipad-pro-11-m4").json()
    assert "ipad-pro-11-m4-wifi-8gb-256gb" in {
        item["slug"] for item in base_body["results"]
    }


def test_mobile_device_unknown_slug_404(client: TestClient) -> None:
    ensure_mobile_device_fixtures()
    response = client.get("/v1/pdas/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
