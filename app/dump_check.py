"""Lightweight validation of a changed static dump against seed record counts."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from app.categories import CATEGORIES, COLLECTIONS


def check_dump(repo: Path, base: str) -> list[str]:
    changed = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD",
         "--", "site/public/v1/"],
        cwd=repo, text=True, capture_output=True, check=True,
    ).stdout.splitlines()
    touched = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD", "--", "site/public/v1/"],
        cwd=repo, text=True, capture_output=True, check=True,
    ).stdout.splitlines()
    if not touched:
        return []
    errors: list[str] = []

    def read(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            errors.append(f"{path.relative_to(repo).as_posix()}: {exc}")
            return None

    for rel in changed:
        if rel.endswith(".json"):
            read(repo / rel)
    root = repo / "site/public/v1"
    manifest = read(root / "index.json")
    collections = manifest.get("collections", {}) if isinstance(manifest, dict) else {}
    if not isinstance(collections, dict):
        errors.append("manifest collections must be an object")
        collections = {}
    for category in CATEGORIES:
        resource = COLLECTIONS[category]
        count = sum(1 for p in (repo / "data" / category).rglob("*.json")
                    if not p.name.startswith("_"))
        entry = collections.get(resource)
        index = read(root / resource / "index.json")
        if not isinstance(entry, dict) or entry.get("count") != count:
            errors.append(f"{resource}: manifest count must equal {count}")
        if (not isinstance(index, dict) or index.get("count") != count
                or not isinstance(index.get("results"), list)
                or len(index["results"]) != count):
            errors.append(f"{resource}: index count/results must equal {count}")
    return errors


def scope(repo: Path, base_ref: str, base_sha: str) -> str:
    count = sum(1 for category in CATEGORIES
                for p in (repo / "data" / category).rglob("*.json")
                if not p.name.startswith("_"))
    return f"12/12 categories ? {count:,} records ? diff base {base_ref}@{base_sha[:7]}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("TechAPI"))
    parser.add_argument("--base", required=True)
    parser.add_argument("--base-ref", default="unknown")
    parser.add_argument("--scope-only", action="store_true")
    args = parser.parse_args()
    print(scope(args.repo, args.base_ref, args.base))
    if args.scope_only:
        return 0
    errors = check_dump(args.repo, args.base)
    for error in errors:
        print(error)
    print(f"Dump JSON/count check: {'FAIL' if errors else 'PASS (or no dump changes)'}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
