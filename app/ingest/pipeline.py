"""Orchestrate ingestion: collect, dedup vs curated, write JSON, summarize."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from app.coverage.curated import curated_slugs

from .sources.base import IngestCandidate


@dataclass
class IngestResult:
    """Summary of one ingestion run."""

    written: list[IngestCandidate] = field(default_factory=list)
    skipped_incomplete: list[IngestCandidate] = field(default_factory=list)
    skipped_existing: list[IngestCandidate] = field(default_factory=list)

    def markdown_summary(self) -> str:
        lines = ["# Ingestion run summary", ""]
        lines.append(f"- written: **{len(self.written)}**")
        lines.append(f"- skipped (already curated): {len(self.skipped_existing)}")
        lines.append(f"- skipped (missing required fields): {len(self.skipped_incomplete)}")
        lines.append("")
        if self.written:
            lines.append("## Added")
            for c in self.written:
                name = c.record.get("name")
                lines.append(
                    f"- `{c.output_path.as_posix()}` — {name} ([source]({c.source_url}))"
                )
            lines.append("")
        if self.skipped_incomplete:
            lines.append("## Skipped (missing required fields)")
            for c in self.skipped_incomplete[:20]:
                missing = ", ".join(c.missing_fields)
                lines.append(f"- `{c.slug}` — missing: {missing} ([source]({c.source_url}))")
            if len(self.skipped_incomplete) > 20:
                lines.append(f"_… and {len(self.skipped_incomplete) - 20} more._")
        return "\n".join(lines).rstrip() + "\n"


def run(
    candidates: Iterable[IngestCandidate],
    *,
    data_root: Path,
    include_drafts: bool = False,
    dry_run: bool = False,
) -> IngestResult:
    """Write each complete (or, with ``include_drafts``, partial) candidate to disk.

    Additions only: candidates whose slug already exists in the curated set
    are skipped. ``dry_run`` runs every check but writes no files.
    """
    result = IngestResult()
    curated_by_category: dict[tuple[str, str], set[str]] = {}
    written_slugs: set[tuple[str, str, str]] = set()

    for candidate in candidates:
        key = (candidate.category, candidate.manufacturer)
        if key not in curated_by_category:
            curated_by_category[key] = curated_slugs(
                candidate.category, candidate.manufacturer
            )
        if candidate.slug in curated_by_category[key]:
            result.skipped_existing.append(candidate)
            continue
        # Avoid emitting two candidates with the same slug in the same run.
        run_key = (candidate.category, candidate.manufacturer, candidate.slug)
        if run_key in written_slugs:
            continue
        if not candidate.is_complete and not include_drafts:
            result.skipped_incomplete.append(candidate)
            continue

        written_slugs.add(run_key)
        target = data_root / candidate.output_path
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(candidate.record, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        result.written.append(candidate)

    return result
