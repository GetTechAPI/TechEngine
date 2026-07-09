"""Generate a static JSON dump from the database (§4.2 data flow, §16.1 dump-data.yml).

The live API responses are written to a tree of static files so a client can
fetch ``dump/v1/smartphones/galaxy-s25/index.json`` without any server. Because
the dump is produced by replaying the real endpoints through an in-process
client, the static files byte-match the live API — zero serialization drift.

Run with: ``python -m app.dump`` (writes to ``./dump`` by default).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "dump"

# Collections that expose list + detail endpoints.
COLLECTIONS = [
    "brands",
    "socs",
    "smartphones",
    "tablets",
    "watches",
    "pdas",
    "gpus",
    "cpus",
    "laptops",
    "monitors",
    "games",
]
# Collections with a /score sub-resource (§8) and a `scored` manifest count.
SCORED = {"smartphones", "cpus", "gpus", "socs"}
PAGE_LIMIT = 100  # API max page size (§7.3)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _fetch_all(client: TestClient, resource: str) -> tuple[int, list[dict[str, Any]]]:
    """Follow pagination to collect every list item for a resource."""
    items: list[dict[str, Any]] = []
    count = 0
    url: str | None = f"/v1/{resource}?limit={PAGE_LIMIT}"
    while url:
        page = client.get(url).json()
        count = page["count"]
        items.extend(page["results"])
        url = page["next"]
    return count, items


def generate(
    client: TestClient,
    output_dir: Path = OUTPUT_DIR,
    collections: list[str] | None = None,
) -> dict[str, int]:
    """Write the full static dump. Returns the number of detail files per collection."""
    counts: dict[str, int] = {}
    manifest: dict[str, object] = {"version": "v1", "collections": {}}

    for resource in collections or COLLECTIONS:
        count, items = _fetch_all(client, resource)
        # Combined list file (un-paginated, convenient for static consumers).
        _write_json(
            output_dir / "v1" / resource / "index.json",
            {"count": count, "results": items},
        )
        scored = 0
        for item in items:
            slug = item["slug"]
            detail = client.get(f"/v1/{resource}/{slug}").json()
            _write_json(output_dir / "v1" / resource / slug / "index.json", detail)
            if resource in SCORED:
                score = client.get(f"/v1/{resource}/{slug}/score").json()
                _write_json(output_dir / "v1" / resource / slug / "score" / "index.json", score)
                if score.get("overall") is not None:
                    scored += 1
        counts[resource] = len(items)
        manifest_collections = manifest["collections"]
        assert isinstance(manifest_collections, dict)
        entry: dict[str, object] = {"count": count, "url": f"/v1/{resource}/index.json"}
        if resource in SCORED:
            entry["scored"] = scored
        manifest_collections[resource] = entry

    _write_json(output_dir / "v1" / "index.json", manifest)

    # Static OpenAPI spec so the docs page (Scalar) works without a server.
    _write_json(output_dir / "openapi.json", client.get("/openapi.json").json())
    return counts


def run(output_dir: Path = OUTPUT_DIR) -> None:
    from sqlmodel import Session

    from app.database import create_db_and_tables, engine
    from app.main import app
    from app.seed import seed

    create_db_and_tables()
    with Session(engine) as session:
        seed(session)
    with TestClient(app) as client:
        counts = generate(client, output_dir)
    total = sum(counts.values())
    print(f"Dumped {total} records to {output_dir}: {counts}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate the TechAPI static JSON dump (§4.2)")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR, help="output directory")
    run(parser.parse_args().output)
