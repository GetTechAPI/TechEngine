"""Tests for the static dump generator (§4.2, §16.1)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.dump import generate
from tests.integration.mobile_device_fixtures import ensure_mobile_device_fixtures


def test_dump_writes_list_detail_and_manifest(client: TestClient, tmp_path: Path) -> None:
    ensure_mobile_device_fixtures()
    collections = ["tablets", "watches", "pdas"]
    counts = generate(client, output_dir=tmp_path, collections=collections)
    assert counts["tablets"] >= 1
    assert counts["watches"] >= 1
    assert counts["pdas"] >= 1

    # Detail file matches the live API response.
    detail_file = tmp_path / "v1" / "tablets" / "ipad-pro-11-m4-wifi-8gb-256gb" / "index.json"
    assert detail_file.exists()
    detail = json.loads(detail_file.read_text())
    assert detail["slug"] == "ipad-pro-11-m4-wifi-8gb-256gb"
    assert detail == client.get("/v1/tablets/ipad-pro-11-m4-wifi-8gb-256gb").json()

    # Combined list file holds every item.
    listing = json.loads((tmp_path / "v1" / "tablets" / "index.json").read_text())
    assert listing["count"] == len(listing["results"])

    # Manifest enumerates all collections.
    manifest = json.loads((tmp_path / "v1" / "index.json").read_text())
    assert set(manifest["collections"].keys()) == set(collections)


def test_dump_writes_scores_and_scored_count(client: TestClient, tmp_path: Path) -> None:
    generate(client, output_dir=tmp_path, collections=["cpus"])
    score_file = tmp_path / "v1" / "cpus" / "core-i9-14900k" / "score" / "index.json"
    assert score_file.exists()
    score = json.loads(score_file.read_text())
    assert score["algorithm_version"] == "2.0.0"
    assert score == client.get("/v1/cpus/core-i9-14900k/score").json()

    manifest = json.loads((tmp_path / "v1" / "index.json").read_text())
    cpus = manifest["collections"]["cpus"]
    assert isinstance(cpus["scored"], int)
    assert 0 <= cpus["scored"] <= cpus["count"]
