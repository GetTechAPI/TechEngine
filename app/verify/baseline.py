"""Render the daily ``status.json`` aggregate as the PR comment's baseline table."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(path: str) -> int:
    p = Path(path)
    if not p.exists():
        print("_status.json not available on this ref._")
        return 0
    d = json.loads(p.read_text(encoding="utf-8"))
    print(f"_As of {d['generated_at']} (daily `verify-status` aggregate)._\n")
    print("| category | total | green | yellow | red |")
    print("| --- | ---: | ---: | ---: | ---: |")
    for cat, c in d["by_category"].items():
        print(f"| {cat} | {c['total']} | {c['green']} | {c['yellow']} | {c['red']} |")
    t = d["totals"]
    print(f"| **all** | {t['records']} | {t['green']} | {t['yellow']} | {t['red']} |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
