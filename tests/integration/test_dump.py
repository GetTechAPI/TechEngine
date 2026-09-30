"""Tests for the static dump generator (§4.2, §16.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.dump import (
    COLLECTIONS,
    _prune_orphaned_pages,
    collections_for_changes,
    generate,
    resolve_collections,
)
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


def test_resolve_collections_defaults_to_everything() -> None:
    assert resolve_collections() == COLLECTIONS
    assert resolve_collections([]) == COLLECTIONS


def test_resolve_collections_drops_excluded_and_keeps_order() -> None:
    resolved = resolve_collections(["software"])
    assert "software" not in resolved
    assert resolved == [c for c in COLLECTIONS if c != "software"]


def test_resolve_collections_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="unknown collection"):
        resolve_collections(["gmaes"])


def test_dump_prunes_output_pages_for_deleted_records(
    client: TestClient, tmp_path: Path
) -> None:
    """A record whose source is gone must lose its output page on the next run.

    Reproduces the real-world bug found during the Atom CPU dedup: a record is
    removed, but re-running the dump used to leave its ``<slug>/`` page tree on
    disk forever. Here we seed fixtures, dump, delete one record from the
    database, re-dump, and assert the deleted record's output directory is gone
    while a surviving record's page (and the collection list file) remain.
    """
    from sqlmodel import Session, select

    from app.database import engine
    from app.models.mobile_device import Tablet

    ensure_mobile_device_fixtures()
    collections = ["tablets"]

    generate(client, output_dir=tmp_path, collections=collections)
    tablets_dir = tmp_path / "v1" / "tablets"
    deleted_slug = "ipad-pro-11-m4-wifi-8gb-256gb"
    deleted_page = tablets_dir / deleted_slug / "index.json"
    assert deleted_page.exists()

    # Delete the record from the database, mimicking a removed source record.
    with Session(engine) as session:
        tablet = session.exec(select(Tablet).where(Tablet.slug == deleted_slug)).one()
        session.delete(tablet)
        session.commit()
    try:
        # Confirm the record is truly gone from the live API before re-dumping.
        assert client.get(f"/v1/tablets/{deleted_slug}").status_code == 404
        surviving = [
            item["slug"]
            for item in client.get("/v1/tablets?limit=100").json()["results"]
        ]
        assert deleted_slug not in surviving

        generate(client, output_dir=tmp_path, collections=collections)

        # The deleted record's whole page directory is pruned...
        assert not (tablets_dir / deleted_slug).exists()
        # ...while the collection list file and any surviving pages remain.
        assert (tablets_dir / "index.json").exists()
        for slug in surviving:
            assert (tablets_dir / slug / "index.json").exists()
    finally:
        # Restore the fixture so later tests relying on it still find the record.
        ensure_mobile_device_fixtures()


def test_prune_orphaned_pages_leaves_files_and_valid_slugs(tmp_path: Path) -> None:
    collection_dir = tmp_path / "v1" / "cpus"
    (collection_dir / "keep-me").mkdir(parents=True)
    (collection_dir / "keep-me" / "index.json").write_text("{}\n", encoding="utf-8")
    (collection_dir / "drop-me").mkdir()
    (collection_dir / "drop-me" / "index.json").write_text("{}\n", encoding="utf-8")
    # The collection's own list file must never be touched (it is not a dir).
    (collection_dir / "index.json").write_text('{"count": 0}\n', encoding="utf-8")

    pruned = _prune_orphaned_pages(collection_dir, valid_slugs={"keep-me"})

    assert pruned == ["drop-me"]
    assert (collection_dir / "keep-me").is_dir()
    assert not (collection_dir / "drop-me").exists()
    assert (collection_dir / "index.json").read_text() == '{"count": 0}\n'


def test_prune_orphaned_pages_noop_when_dir_missing(tmp_path: Path) -> None:
    assert _prune_orphaned_pages(tmp_path / "does-not-exist", valid_slugs=set()) == []


def test_partial_dump_keeps_other_manifest_entries(client: TestClient, tmp_path: Path) -> None:
    generate(client, output_dir=tmp_path, collections=["cpus", "gpus"])
    generate(client, output_dir=tmp_path, collections=["cpus"])
    manifest = json.loads((tmp_path / "v1" / "index.json").read_text())
    assert set(manifest["collections"]) == {"cpus", "gpus"}


def test_resolve_collections_only() -> None:
    assert resolve_collections(only=["laptops", "cpus"]) == ["cpus", "laptops"]
    with pytest.raises(ValueError):
        resolve_collections(only=["nope"])


def test_collections_for_changes_maps_dependents() -> None:
    assert collections_for_changes({"website/a/x.json"}) == ["websites"]
    assert collections_for_changes({"cpu/i/1/x.json"}) == ["cpus", "laptops"]
    assert collections_for_changes({"_verify/status.json"}) == []
    assert collections_for_changes({"brand/us/acme.json"}) == COLLECTIONS
