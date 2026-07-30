"""Integration tests for website endpoints (unscored category)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.website_fixtures import ensure_website_fixtures


def test_list_websites(client: TestClient) -> None:
    ensure_website_fixtures()
    body = client.get("/v1/websites").json()
    assert body["count"] >= 1
    assert "results" in body


def test_website_detail(client: TestClient) -> None:
    ensure_website_fixtures()
    body = client.get("/v1/websites/wikipedia-test").json()
    assert body["slug"] == "wikipedia-test"
    assert body["homepage_url"] == "https://www.wikipedia.org/"
    assert "English" in body["languages"]
    assert "Wikimedia Foundation" in body["owners"]
    # `url` is the API self-link, distinct from the site's own address.
    assert body["url"].endswith("/v1/websites/wikipedia-test")
    # Websites are unscored — no score field.
    assert "score" not in body


def test_website_sort_rejects_unknown_field(client: TestClient) -> None:
    ensure_website_fixtures()
    assert client.get("/v1/websites", params={"sort": "nope"}).status_code == 400


def test_website_not_found(client: TestClient) -> None:
    ensure_website_fixtures()
    assert client.get("/v1/websites/nonexistent-website").status_code == 404
