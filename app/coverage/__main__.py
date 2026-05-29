"""Coverage CLI entry point.

::

    python -m app.coverage [--output coverage-report.md]

Fetches every wired source, diffs the union against the curated dataset
(found via ``TECHAPI_DATA_DIR``), and writes a Markdown report.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from pathlib import Path

from .report import build_report
from .sources.base import CoveragePoint, Source
from .sources.wikipedia_cpu import WikipediaCpu
from .sources.wikipedia_gpu import WikipediaGpu
from .sources.wikipedia_smartphone import WikipediaSmartphone

DEFAULT_SOURCES: list[Source] = [
    WikipediaCpu(),
    WikipediaGpu(),
    WikipediaSmartphone(),
]


def collect(sources: list[Source]) -> Iterator[CoveragePoint]:
    for source in sources:
        yield from source.fetch()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.coverage")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("coverage-report.md"),
        help="Markdown report destination (default: coverage-report.md).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=30,
        help="Max entries per category × manufacturer in the report.",
    )
    args = parser.parse_args(argv)

    points = list(collect(DEFAULT_SOURCES))
    args.output.write_text(build_report(points, top_n=args.top), encoding="utf-8")
    print(f"wrote {args.output} ({len(points)} upstream points)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
