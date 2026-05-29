"""Read curated TechAPI slugs from disk.

Resolves the dataset location the same way as ``app.validate`` / ``app.seed``:
``TECHAPI_DATA_DIR`` env var, falling back to ``../TechAPI/data`` next to this
repo.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def data_dir() -> Path:
    default = Path(__file__).resolve().parent.parent.parent.parent / "TechAPI" / "data"
    return Path(os.environ.get("TECHAPI_DATA_DIR", default))


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
