"""Read curated TechAPI slugs from disk.

Resolves the dataset location the same way as ``app.validate`` / ``app.seed``:
``TECHAPI_DATA_DIR`` env var, falling back to the local TechAPI checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.data_root import get_data_root


def data_dir() -> Path:
    return get_data_root()


def curated_slugs(category: str, manufacturer: str | None = None) -> set[str]:
    """All slugs found under ``data/<category>[/<manufacturer>]/**/*.json``."""
    root = data_dir() / category
    if manufacturer:
        root = root / manufacturer
    if not root.exists():
        return set()
    slugs: set[str] = set()
    for path in root.rglob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        slug = record.get("slug")
        if isinstance(slug, str):
            slugs.add(slug)
    return slugs
