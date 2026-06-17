"""Benchmark enrichment for existing TechAPI records (multi-source).

Unlike ``app.ingest`` (which *adds* missing SKUs), this *enriches* records that
already exist: it fills null benchmark columns on CPU JSONs using a variant-safe
source. It only ever fills nulls (never overwrites) and only writes a chip when
the source confirms an exact heading match; everything else is reported as
"unresolved" for review.

Sources (``--source``):
  * ``passmark``         → passmark_single / passmark_cpu_mark   (cpubenchmark.net)
  * ``cinebench-legacy`` → cinebench_r15/r10/r11_5 single+multi  (technical.city)
  * ``spec-cpu2006``     → specint2006 / specfp2006              (spec.org)

::

    python -m app.ingest.enrich --source cinebench-legacy \\
        --data-root ../TechAPI/data --min-year 2011 --summary enrich.md

Run output is a PR-ready Markdown summary. Designed for the weekly-ingest
workflow, but safe to run locally (respects ``--dry-run`` and ``--sleep``).

DOM note: each source's extractor is validated against live HTML on first run;
adjust selectors if a site's markup drifts. Pure logic is covered by
tests/unit/test_passmark_enrich.py and test_technical_city.py.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.data_root import get_data_root

from .sources import (
    blender,
    cgdirector,
    notebookcheck,
    spec2006,
    technical_city,
    topcpu,
    videocardbenchmark,
)
from .sources.passmark import fetch_scores, make_client

# A resolver maps (client, name, id_override) -> (scores_dict, source_url) | None.
Resolver = Callable[..., "tuple[dict[str, Any], str] | None"]


def _passmark_resolver(
    client: httpx.Client, name: str, id_override: str | None = None
) -> tuple[dict[str, Any], str] | None:
    r = fetch_scores(client, name, id_override=id_override)
    if r is None:
        return None
    return {"passmark_single": r.single_thread, "passmark_cpu_mark": r.cpu_mark}, r.source_url


# name -> (resolver, primary_field). primary_field skips records already filled;
# None means "attempt every record" (for multi-field sources — fill-only-nulls
# still applies, and cached-table sources cost no network per record).
SOURCES: dict[str, tuple[Resolver, str | None]] = {
    "passmark": (_passmark_resolver, "passmark_cpu_mark"),
    "cinebench-legacy": (technical_city.resolve, "cinebench_r15_multi"),
    "cinebench-r23": (cgdirector.resolve, "cinebench_r23_multi"),
    "cinebench-2024": (cgdirector.resolve_2024, "cinebench_2024_multi"),
    "cinebench-nbc": (notebookcheck.resolve, None),
    "geekbench-nbc": (notebookcheck.resolve_geekbench, "geekbench_multi"),
    "spec-cpu2006": (spec2006.resolve, None),
    "blender": (blender.resolve, "blender_score"),  # GPU: --component gpu
    "timespy": (topcpu.resolve, "timespy_score"),  # GPU: --component gpu
    "topcpu-cpu": (topcpu.resolve_cpu, None),  # CPU: cb2024/passmark/gb6/r23 fill
    "passmark-gpu": (videocardbenchmark.resolve, "passmark_g3d_mark"),  # GPU: legacy-incl.
    "topcpu-gpu": (topcpu.resolve_gpu, None),  # GPU: timespy-extreme/speedway/octane/fp32
}


@dataclass
class EnrichResult:
    filled: list[tuple[str, dict[str, Any]]] = field(default_factory=list)  # (slug, scores)
    unresolved: list[str] = field(default_factory=list)
    already: int = 0

    def markdown_summary(self, source: str = "") -> str:
        lines = [f"# Benchmark enrichment summary ({source})".rstrip(), ""]
        lines.append(f"- filled: **{len(self.filled)}**")
        lines.append(f"- unresolved (no exact-variant match / no data): {len(self.unresolved)}")
        lines.append(f"- skipped (already populated): {self.already}")
        lines.append("")
        if self.filled:
            lines.append("## Filled")
            for slug, scores in self.filled:
                vals = ", ".join(f"{k}={v}" for k, v in scores.items())
                lines.append(f"- `{slug}` — {vals}")
            lines.append("")
        if self.unresolved:
            lines.append("## Unresolved (no exact match or source lacks the data)")
            for name in self.unresolved:
                lines.append(f"- {name}")
        return "\n".join(lines).rstrip() + "\n"


def _default_data_root() -> Path:
    return get_data_root()


def _candidates(cpu_root: Path, manufacturer: str | None) -> list[Path]:
    base = cpu_root / manufacturer if manufacturer else cpu_root
    return sorted(p for p in base.rglob("*.json") if not p.name.startswith("_"))


def enrich(
    *,
    data_root: Path,
    resolver: Resolver = _passmark_resolver,
    primary_field: str | None = "passmark_cpu_mark",
    component: str = "cpu",
    manufacturer: str | None = None,
    limit: int | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    overrides: dict[str, str] | None = None,
    sleep: float = 1.0,
    dry_run: bool = False,
) -> EnrichResult:
    overrides = overrides or {}
    result = EnrichResult()
    client = make_client()
    processed = 0
    try:
        for path in _candidates(data_root / component, manufacturer):
            rec = json.loads(path.read_text(encoding="utf-8"))
            if primary_field is not None and rec.get(primary_field) is not None:
                result.already += 1
                continue
            year = (rec.get("release_date") or "0")[:4]
            if min_year is not None and year < str(min_year):
                continue
            if max_year is not None and year > str(max_year):
                continue
            if limit is not None and processed >= limit:
                break
            processed += 1
            name = rec.get("name", "")
            out = resolver(client, name, overrides.get(name))
            if sleep:
                time.sleep(sleep)
            if out is None:
                result.unresolved.append(name)
                continue
            scores, source_url = out
            changed = {k: v for k, v in scores.items() if rec.get(k) is None}
            if not changed:
                result.already += 1
                continue
            rec.update(changed)
            urls = rec.setdefault("source_urls", [])
            if source_url not in urls:
                urls.append(source_url)
            if not dry_run:
                path.write_text(
                    json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                )
            result.filled.append((rec.get("slug", path.stem), changed))
    finally:
        if client is not None:
            client.close()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.ingest.enrich")
    parser.add_argument("--source", choices=sorted(SOURCES), default="passmark")
    parser.add_argument("--data-root", type=Path, default=_default_data_root())
    parser.add_argument(
        "--component", default="cpu", help="Component dir under data-root (cpu, gpu)."
    )
    parser.add_argument(
        "--manufacturer", default=None, help="Limit to data/<component>/<manufacturer>/."
    )
    parser.add_argument("--limit", type=int, default=None, help="Max records to query this run.")
    parser.add_argument("--min-year", type=int, default=None, help="Skip records before this year.")
    parser.add_argument("--max-year", type=int, default=None, help="Skip records after this year.")
    parser.add_argument(
        "--overrides", type=Path, default=None, help="JSON map {name: passmark_id}."
    )
    parser.add_argument("--sleep", type=float, default=1.0, help="Seconds between requests.")
    parser.add_argument("--summary", type=Path, default=Path("enrich-summary.md"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    overrides: dict[str, str] = {}
    if args.overrides and args.overrides.exists():
        overrides = json.loads(args.overrides.read_text(encoding="utf-8"))

    resolver, primary_field = SOURCES[args.source]
    result = enrich(
        data_root=args.data_root,
        resolver=resolver,
        primary_field=primary_field,
        component=args.component,
        manufacturer=args.manufacturer,
        limit=args.limit,
        min_year=args.min_year,
        max_year=args.max_year,
        overrides=overrides,
        sleep=args.sleep,
        dry_run=args.dry_run,
    )
    args.summary.write_text(result.markdown_summary(args.source), encoding="utf-8")
    print(
        f"source={args.source} filled={len(result.filled)} "
        f"unresolved={len(result.unresolved)} already={result.already} dry_run={args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
