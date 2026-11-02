"""Orchestrate ingestion: collect, dedup vs curated, write JSON, summarize.

Additions only, and only when the values can be trusted: a candidate that
might already be on file, might not have shipped, or carries a value the
source could not pin down is skipped with its reason in the summary.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

from app.coverage.normalize import slugify

from .cpu_identity import cpu_key, same_cpu
from .gpu_identity import (
    datacenter_family,
    form_factor_of,
    gpu_family_key,
    gpu_key,
    spec_twins,
)
from .sources.base import IngestCandidate

# Rows of the "Skipped" lists printed in the summary; counts are always complete.
_LIST_LIMIT = 30


@dataclass
class IngestResult:
    """Summary of one ingestion run."""

    written: list[IngestCandidate] = field(default_factory=list)
    skipped_incomplete: list[IngestCandidate] = field(default_factory=list)
    skipped_existing: list[IngestCandidate] = field(default_factory=list)
    # Rows that repeat a record this run already wrote (a second table row for
    # the same board, a laptop row listed on two pages).
    skipped_same_run: list[IngestCandidate] = field(default_factory=list)
    # Why each skipped candidate was skipped, keyed by the candidate's id():
    # one page can list the same slug twice, once complete and once not.
    reasons: dict[int, str] = field(default_factory=dict)

    def reason(self, candidate: IngestCandidate) -> str:
        return self.reasons.get(id(candidate), "")

    def markdown_summary(self) -> str:
        lines = ["# Ingestion run summary", ""]
        lines.append(f"- written: **{len(self.written)}**")
        lines.append(f"- skipped (already curated): {len(self.skipped_existing)}")
        lines.append(f"- skipped (repeats a row added in this run): {len(self.skipped_same_run)}")
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
        if self.skipped_same_run:
            lines.append("## Skipped (repeats a row added in this run)")
            lines.extend(
                f"- `{c.slug}` — {self.reason(c)}" for c in self.skipped_same_run[:_LIST_LIMIT]
            )
            if len(self.skipped_same_run) > _LIST_LIMIT:
                lines.append(f"_… and {len(self.skipped_same_run) - _LIST_LIMIT} more._")
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
        names: list[object] = [slug]
        # The curated name can be spelled unlike the slug: "3D Rage II" is
        # rage-ii.json, "iPhone SE (2nd generation)" iphone-se-2020.json.
        display = record.get("name")
        if isinstance(display, str) and display:
            names.append(slugify(display, manufacturer=manufacturer))
        if self.category == "smartphone":
            # A variant record (".../2022/galaxy-m33-5g/sm-m336b.json") names
            # its phone in base_model_slug and in its folder.
            names.append(record.get("base_model_slug"))
            parts = path.split("/")
            if len(parts) == 5:
                names.append(parts[3])
        for name in names:
            if isinstance(name, str) and name:
                self.slugs.setdefault(name, path)
                self.keys.setdefault(self._key(name), path)
                if self.category == "gpu":
                    self.families.setdefault(gpu_family_key(name), path)

    def _key(self, slug: str) -> str:
        if self.category == "gpu":
            return gpu_key(slug)
        tokens = slug.split("-")
        if self.category == "smartphone":
            # "iPhone SE (2nd)" and "iPhone SE (2nd generation)".
            tokens = [token for token in tokens if token not in {"gen", "generation"}]
        return "".join(tokens)

    def match(self, candidate: IngestCandidate) -> tuple[str, str] | None:
        """``(path, why)`` of the record the candidate or one of its
        ``aliases`` duplicates, or ``None``."""
        if (found := self._match(candidate)) is not None:
            return found
        for alias in candidate.aliases:
            if (found := self._match(replace(candidate, slug=alias))) is not None:
                path, why = found
                return path, f"as {alias}: {why}"
        return None

    def _match(self, candidate: IngestCandidate) -> tuple[str, str] | None:
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
            return path, f"a variant of {path} (bus, memory, OEM or edition name)"
        if (base := datacenter_family(candidate.slug)) is not None:
            children = sorted(
                path for key, path in self.families.items() if form_factor_of(key, base)
            )
            if children:
                return children[0], (
                    f"names the board family of {', '.join(children)}; which one is unclear"
                )
        for entry in self.records:
            if spec_twins(candidate.record, entry.record):
                return entry.path, f"likely the same board as {entry.path} (same clock and TDP)"
        return None


# Fields that name or file a record rather than describe the part.
_IDENTITY_FIELDS = frozenset({"slug", "name", "manufacturer", "verified", "source_urls"})


def _variant_conflicts(candidates: list[IngestCandidate]) -> dict[int, str]:
    """Rows that share a slug but disagree on a value, keyed by ``id()``.

    A page often lists one board name per variant ("GeForce GT 1030" with
    GDDR5 and with DDR4, "GeForce RTX 3060" with 12 and 8 GB), and two pages
    can disagree on one part. Which row is the part the slug names is then
    unclear, so none of them is written.
    """
    groups: dict[tuple[str, str], list[IngestCandidate]] = {}
    for candidate in candidates:
        groups.setdefault((candidate.category, candidate.slug), []).append(candidate)
    conflicts: dict[int, str] = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        names = set().union(*(c.record for c in group)) - _IDENTITY_FIELDS
        fields = sorted(
            name
            for name in names
            if len({json.dumps(c.record[name]) for c in group if c.record.get(name) is not None})
            > 1
        )
        if fields:
            why = f"the source lists {len(group)} rows named {group[0].slug} with different " + (
                ", ".join(fields)
            )
            conflicts.update((id(c), why) for c in group)
    return conflicts


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
    by ``run_date`` (default: today), that carries ``problems`` or whose slug
    another row of the run lists with different values is skipped even with
    ``include_drafts``. ``dry_run`` runs every check but writes no files.
    """
    run_date = run_date or date.today()
    candidates = list(candidates)
    conflicts = _variant_conflicts(candidates)
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
                skip(result.skipped_same_run, candidate, f"{why}, added in this run")
                continue
            skip(result.skipped_existing, candidate, why)
            continue
        if (unreleased := _not_released(candidate, run_date)) is not None:
            skip(result.skipped_incomplete, candidate, unreleased)
            continue
        problems = candidate.problems + (
            (conflicts[id(candidate)],) if id(candidate) in conflicts else ()
        )
        if problems:
            skip(result.skipped_incomplete, candidate, "uncertain: " + "; ".join(problems))
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
