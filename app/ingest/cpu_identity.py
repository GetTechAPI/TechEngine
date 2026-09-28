"""Conservative, symmetric CPU identity checks for additions-only ingest."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.coverage.normalize import slugify


def cpu_key(value: str, manufacturer: str) -> str:
    tokens = slugify(value, manufacturer=manufacturer).split("-")
    if tokens and tokens[0] == manufacturer:
        tokens.pop(0)
    # Wikipedia uses both "1700X PRO" and "PRO 1700X" for the same SKU.
    if "pro" in tokens:
        tokens = [token for token in tokens if token != "pro"] + ["pro"]
    return "".join(tokens)


def same_cpu(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    short, long = sorted((left, right), key=len)
    if len(short) < 4 or not long.endswith(short):
        return False
    # Bare 1200 must not match 41200. Suffixes (X, U, HE, PRO) remain identity.
    prefix = long[: -len(short)]
    return (
        not (short[0].isdigit() and prefix[-1].isdigit())
        or re.search(r"[a-z]\d{1,2}$", prefix) is not None
    )


def curated_cpu_keys(data_root: Path, manufacturer: str) -> set[str]:
    keys: set[str] = set()
    for path in (data_root / "cpu" / manufacturer).rglob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        for field in ("slug", "name"):
            value = record.get(field)
            if isinstance(value, str):
                keys.add(cpu_key(value, manufacturer))
    return keys
