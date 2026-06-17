"""Resolve the TechAPI data directory for sibling and submodule layouts."""

from __future__ import annotations

import os
from pathlib import Path


def get_data_root() -> Path:
    explicit = os.environ.get("TECHAPI_DATA_DIR")
    if explicit:
        return Path(explicit)

    repo_parent = Path(__file__).resolve().parent.parent.parent
    candidates = (
        repo_parent / "data",
        repo_parent / "TechAPI" / "data",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]
