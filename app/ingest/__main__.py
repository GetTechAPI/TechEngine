"""Ingestion CLI.

::

    python -m app.ingest --category cpu --limit 5 \\
        --data-root ../TechAPI/data --summary ingest-summary.md

Streams candidates from every wired source for ``--category``, dedups
against the curated dataset, writes additions to ``--data-root``, and
emits a Markdown summary (used by the weekly workflow as the PR body).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from pathlib import Path

from app.data_root import get_data_root

from .pipeline import run
from .sources.base import IngestCandidate, IngestSource
from .sources.wikipedia_cpu import WikipediaCpuIngest
from .sources.wikipedia_gpu import WikipediaGpuIngest
from .sources.wikipedia_smartphone import WikipediaSmartphoneIngest

SOURCES_BY_CATEGORY: dict[str, list[IngestSource]] = {
    "cpu": [WikipediaCpuIngest()],
    "gpu": [WikipediaGpuIngest()],
    "smartphone": [WikipediaSmartphoneIngest()],
}


def _default_data_root() -> Path:
    return get_data_root()


def _collect(category: str, limit: int | None) -> Iterator[IngestCandidate]:
    for source in SOURCES_BY_CATEGORY.get(category, []):
        yield from source.fetch(limit=limit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.ingest")
    parser.add_argument(
        "--category", required=True, choices=sorted(SOURCES_BY_CATEGORY.keys())
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Max candidates to consider per source."
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=_default_data_root(),
        help="Path to the TechAPI ``data/`` directory.",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("ingest-summary.md"),
        help="Markdown summary destination (PR body).",
    )
    parser.add_argument(
        "--include-drafts",
        action="store_true",
        help="Write records that are missing required fields too (PR will be a draft).",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Compute everything; write nothing."
    )
    args = parser.parse_args(argv)

    candidates = list(_collect(args.category, args.limit))
    result = run(
        candidates,
        data_root=args.data_root,
        include_drafts=args.include_drafts,
        dry_run=args.dry_run,
    )
    args.summary.write_text(result.markdown_summary(), encoding="utf-8")
    print(
        f"category={args.category} considered={len(candidates)} "
        f"written={len(result.written)} "
        f"skipped_existing={len(result.skipped_existing)} "
        f"skipped_incomplete={len(result.skipped_incomplete)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
