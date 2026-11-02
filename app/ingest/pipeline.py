"""Orchestrate ingestion: collect, dedup vs curated, write JSON, summarize.

Additions only, and only when the values can be trusted: a candidate that
might already be on file, might not have shipped, or carries a value the
source could not pin down is skipped with its reason in the summary.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .cpu_identity import cpu_key, same_cpu
from .gpu_identity import gpu_family_key, gpu_key, spec_twins
from .sources.base import IngestCandidate

# Rows of the "Skipped" lists printed in the summary; counts are always complete.
_LIST_LIMIT = 30


@dataclass
class IngestResult:
    """Summary of one ingestion run."""

    written: list[IngestCandidate] = field(default_factory=list)
    skipped_incomplete: list[IngestCandidate] = field(default_factory=list)
    skipped_existing: list[IngestCandidate] = field(default_factory=list)
    # Why each skipped candidate was skipped, keyed by the candidate's id():
    # one page can list the same slug twice, once complete and once not.
    reasons: dict[int, str] = field(default_factory=dict)

    def reason(self, candidate: IngestCandidate) -> str:
        return self.reasons.get(id(candidate), "")

    def markdown_summary(self) -> str:
        lines = ["# Ingestion run summary", ""]
        lines.append(f"- written: **{len(self.written)}**")
        lines.append(f"- skipped (already curated): {len(self.skipped_existing)}")
        lines.append(f"- skipped (incomplete or uncertain): {len(self.skipped_incomplete)}")
        lines.append("")
        if self.written:
            lines.append("## Added")
            for c in self.written:
                name = c.record.get("name")
                lines.append(
                    f"- `{c.output_path.as_posix()}` — {name} ([source]({c.source_url}))"
                )
            lines.append("")
        # Exact slug matches are routine; a match under another name is worth a look.
        renamed = [c for c in self.skipped_existing if not self.reason(c).startswith("slug ")]
        if renamed:
            lines.append("## Skipped (already curated under another name)")
            lines.extend(f"- `{c.slug}` — {self.reason(c)}" for c in renamed[:_LIST_LIMIT])
            if len(renamed) > _LIST_LIMIT:
                lines.append(f"_… and {len(renamed) - _LIST_LIMIT} more._")
            lines.append("")
        if self.skipped_incomplete:
            lines.append("## Skipped (incomplete or uncertain)")
            counts = Counter(self.reason(c).split(":")[0] for c in self.skipped_incomplete)
            lines.extend(f"- {n} × {kind}" for kind, n in counts.most_common())
            lines.append("")
            for c in self.skipped_incomplete[:_LIST_LIMIT]:
                lines.append(f"- `{c.slug}` — {self.reason(c)} ([source]({c.source_url}))")
            if len(self.skipped_incomplete) > _LIST_LIMIT:
                lines.append(f"_… and {len(self.skipped_incomplete) - _LIST_LIMIT} more._")
        return "\n".join(lines).rstrip() + "\n"


@dataclass
class _OnFile:
    """One record already in the category, from any manufacturer folder."""

    path: str
    manufacturer: str
    record: dict[str, Any]


def _on_file(data_root: Path, category: str) -> list[_OnFile]:
    root = data_root / category
    found: list[_OnFile] = []
    for path in sorted(root.rglob("*.json")) if root.is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        relative = path.relative_to(data_root)
        manufacturer = str(record.get("manufacturer") or relative.parts[1])
        found.append(_OnFile(relative.as_posix(), manufacturer, record))
    return found


class _Index:
    """What is already curated in one category, under every spelling we check."""

    def __init__(self, category: str, on_file: list[_OnFile]) -> None:
        self.category = category
        self.records = on_file
        self.paths: set[str] = set()
        self.slugs: dict[str, str] = {}
        self.keys: dict[str, str] = {}
        self.families: dict[str, str] = {}
        self.cpu_keys: list[tuple[str, str]] = []
        for entry in on_file:
            self.add(entry.record, entry.path, entry.manufacturer)

    def add(self, record: dict[str, Any], path: str, manufacturer: str) -> None:
        self.paths.add(path)
        slug = record.get("slug")
        if self.category == "cpu":
            for value in (slug, record.get("name")):
                if isinstance(value, str) and value:
                    self.cpu_keys.append((cpu_key(value, manufacturer), path))
            return
        if not isinstance(slug, str) or not slug:
            return
        self.slugs.setdefault(slug, path)
        self.keys.setdefault(self._key(slug), path)
        if self.category == "gpu":
            self.families.setdefault(gpu_family_key(slug), path)

    def _key(self, slug: str) -> str:
        return gpu_key(slug) if self.category == "gpu" else slug.replace("-", "")

    def match(self, candidate: IngestCandidate) -> tuple[str, str] | None:
        """``(path, why)`` of the record the candidate duplicates, or ``None``."""
        target = candidate.output_path.as_posix()
        if target in self.paths:
            return target, f"path {target} already exists"
        if candidate.slug in self.slugs:
            path = self.slugs[candidate.slug]
            return path, f"slug matches {path}"
        if self.category == "cpu":
            identity = cpu_key(candidate.slug, candidate.manufacturer)
            for other, path in self.cpu_keys:
                if same_cpu(identity, other):
                    return path, f"same CPU as {path}"
            return None
        if (key := self._key(candidate.slug)) in self.keys:
            path = self.keys[key]
            return path, f"same name as {path}"
        if self.category != "gpu":
            return None
        if (family := gpu_family_key(candidate.slug)) in self.families:
            path = self.families[family]
            return path, f"a variant of {path} (bus, memory, laptop or edition name)"
        for entry in self.records:
            if spec_twins(candidate.record, entry.record):
                return entry.path, f"likely the same board as {entry.path} (same clock and TDP)"
        return None


def _not_released(candidate: IngestCandidate, run_date: date) -> str | None:
    released_by = candidate.released_by
    if released_by is None:
        raw = candidate.record.get("release_date")
        try:
            released_by = date.fromisoformat(raw) if isinstance(raw, str) else None
        except ValueError:
            released_by = None
    if released_by is not None and released_by > run_date:
        return (
            f"not released by {run_date.isoformat()}: "
            f"the source dates it to {released_by.isoformat()} or earlier"
        )
    return None


def run(
    candidates: Iterable[IngestCandidate],
    *,
    data_root: Path,
    include_drafts: bool = False,
    dry_run: bool = False,
    run_date: date | None = None,
) -> IngestResult:
    """Write each trustworthy, complete (or, with ``include_drafts``, partial) candidate.

    Additions only: a candidate already on file in its category, under any
    manufacturer folder and any spelling ``_Index`` checks, is skipped, and an
    existing path is never written over. A candidate that may not have shipped
    by ``run_date`` (default: today) or that carries ``problems`` is skipped
    even with ``include_drafts``. ``dry_run`` runs every check but writes no files.
    """
    run_date = run_date or date.today()
    result = IngestResult()
    indexes: dict[str, _Index] = {}
    written_paths: set[str] = set()

    def skip(bucket: list[IngestCandidate], candidate: IngestCandidate, why: str) -> None:
        bucket.append(candidate)
        result.reasons[id(candidate)] = why

    for candidate in candidates:
        if candidate.category not in indexes:
            indexes[candidate.category] = _Index(
                candidate.category, _on_file(data_root, candidate.category)
            )
        index = indexes[candidate.category]
        if (on_file := index.match(candidate)) is not None:
            path, why = on_file
            if path in written_paths:
                continue  # a second row for a record this run already wrote
            skip(result.skipped_existing, candidate, why)
            continue
        if (unreleased := _not_released(candidate, run_date)) is not None:
            skip(result.skipped_incomplete, candidate, unreleased)
            continue
        if candidate.problems:
            why = "uncertain: " + "; ".join(candidate.problems)
            skip(result.skipped_incomplete, candidate, why)
            continue
        if not candidate.is_complete and not include_drafts:
            skip(
                result.skipped_incomplete,
                candidate,
                "missing: " + ", ".join(candidate.missing_fields),
            )
            continue

        target = data_root / candidate.output_path
        # Later candidates in the same run are checked against this one too.
        path = candidate.output_path.as_posix()
        written_paths.add(path)
        index.records.append(_OnFile(path, candidate.manufacturer, candidate.record))
        index.add(candidate.record, path, candidate.manufacturer)
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(candidate.record, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        result.written.append(candidate)

    return result
