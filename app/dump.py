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
import shutil
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.categories import COLLECTIONS as CATEGORY_COLLECTIONS

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "dump"

# Collections that expose list + detail endpoints.
COLLECTIONS = list(CATEGORY_COLLECTIONS.values())
# Collections with a /score sub-resource (§8) and a `scored` manifest count.
SCORED = {"smartphones", "cpus", "gpus", "socs"}
PAGE_LIMIT = 100  # API max page size (§7.3)


def resolve_collections(exclude: list[str] | None = None) -> list[str]:
    """Return the collections to dump, minus ``exclude``.

    Unknown names raise instead of being ignored, so a typo in a workflow fails
    loudly rather than silently dumping everything.
    """
    if not exclude:
        return list(COLLECTIONS)
    unknown = sorted(set(exclude) - set(COLLECTIONS))
    if unknown:
        raise ValueError(
            f"unknown collection(s) {unknown}; valid names: {', '.join(COLLECTIONS)}"
        )
    return [resource for resource in COLLECTIONS if resource not in set(exclude)]


def _write_json(path: Path, data: object) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    # Leave identical pages untouched: rewriting ~1M unchanged files resets
    # their mtimes, and git then rehashes the whole tree when committing.
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _prune_orphaned_pages(collection_dir: Path, valid_slugs: set[str]) -> list[str]:
    """Remove per-record page directories that no longer back a current record.

    The dump writes each record to ``<collection_dir>/<slug>/index.json`` (and,
    for scored collections, ``<slug>/score/index.json``). When a source record
    is deleted or renamed, its old ``<slug>/`` directory is otherwise left on
    disk forever. This deletes any immediate child *directory* of
    ``collection_dir`` whose name is not in ``valid_slugs``.

    Only per-slug subdirectories the dump itself owns are touched. The
    collection's own ``index.json`` list file (and any other non-directory
    entry) is left alone, so top-level manifests, ``openapi.json``, etc. are
    never at risk — this only ever runs inside a per-category directory.
    """
    if not collection_dir.is_dir():
        return []
    pruned: list[str] = []
    for child in collection_dir.iterdir():
        if not child.is_dir():
            continue
        if child.name in valid_slugs:
            continue
        shutil.rmtree(child)
        pruned.append(child.name)
    return pruned


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
        # Self-heal: drop any per-record page directory whose source record no
        # longer exists, so the dump stays a deterministic mirror of the data.
        valid_slugs = {item["slug"] for item in items}
        _prune_orphaned_pages(output_dir / "v1" / resource, valid_slugs)
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


def run(output_dir: Path = OUTPUT_DIR, exclude: list[str] | None = None) -> None:
    from sqlmodel import Session

    from app.database import create_db_and_tables, engine
    from app.main import app
    from app.seed import seed

    collections = resolve_collections(exclude)

    create_db_and_tables()
    with Session(engine) as session:
        seed(session)
    with TestClient(app) as client:
        counts = generate(client, output_dir, collections)
    total = sum(counts.values())
    skipped = sorted(set(COLLECTIONS) - set(collections))
    suffix = f" (skipped: {', '.join(skipped)})" if skipped else ""
    print(f"Dumped {total} records to {output_dir}: {counts}{suffix}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate the TechAPI static JSON dump (§4.2)")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR, help="output directory")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="COLLECTION",
        help=(
            "collection to skip, repeatable (e.g. --exclude software). Useful when a "
            "consumer does not publish a large collection: skipping it avoids "
            "writing hundreds of thousands of files that are discarded anyway."
        ),
    )
    args = parser.parse_args()
    run(args.output, args.exclude)
