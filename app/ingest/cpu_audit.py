"""Read-only, reproducible CPU ingest audit; never writes to the dataset.

Example::

    python -m app.ingest.cpu_audit --page List_of_AMD_Ryzen_processors \
        --page List_of_AMD_Opteron_processors --data-root ../TechAPI/data \
        --output amd-cpu-dry-run.json

``--html-dir`` replays previously downloaded HTML instead of fetching pages.
The JSON includes every proposed record and every unresolved unique model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.coverage.sources.wikipedia import fetch_wikipedia_html
from app.coverage.sources.wikipedia_cpu import WikipediaCpu

from .pipeline import run
from .sources.base import IngestCandidate
from .sources.wikipedia_cpu import PAGES, WikipediaCpuIngest


def _entry(candidate: IngestCandidate) -> dict[str, object]:
    return {
        "output_path": candidate.output_path.as_posix(),
        "record": candidate.record,
        "missing_fields": list(candidate.missing_fields),
    }


def _coverage_audit(
    html_by_page: dict[str, str],
    candidates: list[IngestCandidate],
    ready: set[str],
    existing: set[str],
) -> dict[str, object]:
    points = {
        point.slug: point
        for page, html in html_by_page.items()
        for point in WikipediaCpu._extract(html, "amd", page)
    }
    entries = []
    for slug, point in sorted(points.items()):
        matches = [
            c
            for c in candidates
            if c.source_url == point.url and (c.slug == slug or c.slug.endswith("-" + slug))
        ]
        if not any(c.source_url == point.url for c in candidates):
            status = "outside_requested_pages"
        elif any(c.slug in ready for c in matches):
            status = "ready_to_add"
        elif any(c.slug in existing for c in matches):
            status = "already_curated"
        elif matches:
            status = "missing_required_specs"
        else:
            status = "non_model_or_unparsed_cell"
        entries.append(
            {
                "coverage_slug": slug,
                "source_url": point.url,
                "status": status,
                "candidate_slugs": sorted({c.slug for c in matches}),
                "missing_fields": sorted({field for c in matches for field in c.missing_fields}),
            }
        )
    return {
        "total": len(points),
        "counts": dict(Counter(entry["status"] for entry in entries)),
        "entries": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", action="append", required=True, choices=[p[1] for p in PAGES])
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--html-dir", type=Path)
    parser.add_argument(
        "--coverage-page",
        action="append",
        default=[],
        help="Optional AMD pages whose raw coverage entries should be reconciled.",
    )
    args = parser.parse_args(argv)
    if not args.data_root.is_dir():
        parser.error("--data-root must be an existing TechAPI data directory")
    candidates: list[IngestCandidate] = []
    sources = []
    html_by_page: dict[str, str] = {}
    for manufacturer, page, family in PAGES:
        if page not in args.page:
            continue
        html = (
            (args.html_dir / f"{page}.html").read_text(encoding="utf-8")
            if args.html_dir
            else fetch_wikipedia_html(page)
        )
        sources.append(
            {
                "url": f"https://en.wikipedia.org/wiki/{page}",
                "html_sha256": hashlib.sha256(html.encode()).hexdigest(),
            }
        )
        html_by_page[page] = html
        candidates.extend(WikipediaCpuIngest._extract(html, manufacturer, page, family))
    result = run(candidates, data_root=args.data_root, dry_run=True)
    existing = {c.slug: c for c in result.skipped_existing}
    incomplete = {c.slug: c for c in result.skipped_incomplete if c.slug not in existing}
    missing = Counter(field for c in incomplete.values() for field in c.missing_fields)
    snapshot = hashlib.sha256()
    manufacturers = {c.manufacturer for c in candidates}
    curated_paths = sorted(
        path for maker in manufacturers for path in (args.data_root / "cpu" / maker).rglob("*.json")
    )
    for path in curated_paths:
        snapshot.update(path.relative_to(args.data_root).as_posix().encode())
        snapshot.update(b"\0")
        snapshot.update(path.read_bytes())
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dry_run": True,
        "include_drafts": False,
        "sources": sources,
        "curated_cpu_snapshot": {
            "records": len(curated_paths),
            "sha256": snapshot.hexdigest(),
        },
        "counts": {
            "candidate_rows": len(candidates),
            "unique_models": len({c.slug for c in candidates}),
            "would_add": len(result.written),
            "already_existing": len(existing),
            "incomplete": len(incomplete),
            "missing_fields": dict(missing),
        },
        "would_add": [_entry(c) for c in result.written],
        "already_existing": sorted(existing),
        "incomplete": [
            {"slug": c.slug, "missing_fields": list(c.missing_fields), "source_url": c.source_url}
            for c in incomplete.values()
        ],
    }
    if args.coverage_page:
        for page in args.coverage_page:
            if page not in html_by_page:
                html_by_page[page] = (
                    (args.html_dir / f"{page}.html").read_text(encoding="utf-8")
                    if args.html_dir
                    else fetch_wikipedia_html(page)
                )
        payload["coverage_reconciliation"] = _coverage_audit(
            {page: html_by_page[page] for page in args.coverage_page},
            candidates,
            {c.slug for c in result.written},
            set(existing),
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload["counts"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
