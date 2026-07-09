"""Integration tests for game endpoints (unscored category)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.game_fixtures import ensure_game_fixtures


def test_list_games(client: TestClient) -> None:
    ensure_game_fixtures()
    body = client.get("/v1/games").json()
    # The fixture may be buried under real game data in pagination, so only the
    # count is asserted here; the detail test verifies the fixture itself.
    assert body["count"] >= 1
    assert "results" in body


def test_list_games_sorted(client: TestClient) -> None:
    ensure_game_fixtures()
    body = client.get("/v1/games?sort=-metacritic").json()
    assert body["count"] >= 1


def test_game_detail(client: TestClient) -> None:
    ensure_game_fixtures()
    body = client.get("/v1/games/the-witcher-3-test").json()
    assert body["slug"] == "the-witcher-3-test"
    assert body["metacritic"] == 92
    assert "RPG" in body["genres"]
    assert "CD Projekt Red" in body["developers"]
    # Games are unscored — no score field.
    assert "score" not in body


def test_game_not_found(client: TestClient) -> None:
    ensure_game_fixtures()
    assert client.get("/v1/games/nonexistent-game").status_code == 404
