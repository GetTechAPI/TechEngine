"""Pipeline behavior tests with synthetic candidates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ingest.pipeline import run
from app.ingest.sources.base import IngestCandidate


def _candidate(
    slug: str,
    missing: tuple[str, ...] = (),
    *,
    manufacturer: str = "intel",
) -> IngestCandidate:
    return IngestCandidate(
        category="cpu",
        manufacturer=manufacturer,
        slug=slug,
        record={"slug": slug, "name": slug, "manufacturer": manufacturer},
        source_url=f"https://example.test/{slug}",
        output_path=Path("cpu") / manufacturer / "2024" / "consumer" / f"{slug}.json",
        missing_fields=missing,
    )


def test_writes_complete_candidates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    result = run([_candidate("core-i9-test")], data_root=tmp_path)
    assert len(result.written) == 1
    written = tmp_path / "cpu" / "intel" / "2024" / "consumer" / "core-i9-test.json"
    assert written.exists()
    assert json.loads(written.read_text(encoding="utf-8"))["slug"] == "core-i9-test"


def test_skips_existing_curated_slugs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    existing_dir = tmp_path / "cpu" / "intel"
    existing_dir.mkdir(parents=True)
    (existing_dir / "have.json").write_text(json.dumps({"slug": "have"}), encoding="utf-8")

    result = run([_candidate("have"), _candidate("new")], data_root=tmp_path)
    assert [c.slug for c in result.written] == ["new"]
    assert [c.slug for c in result.skipped_existing] == ["have"]


def test_skips_incomplete_unless_drafts_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    incomplete = _candidate("partial", missing=("release_date",))
    result = run([incomplete], data_root=tmp_path)
    assert [c.slug for c in result.skipped_incomplete] == ["partial"]
    assert not result.written

    result_with_drafts = run([incomplete], data_root=tmp_path, include_drafts=True)
    assert [c.slug for c in result_with_drafts.written] == ["partial"]


def test_dry_run_writes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    result = run([_candidate("dry")], data_root=tmp_path, dry_run=True)
    assert [c.slug for c in result.written] == ["dry"]
    assert not (tmp_path / "cpu" / "intel" / "2024" / "consumer" / "dry.json").exists()


def test_dedups_repeat_slug_in_same_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    result = run(
        [_candidate("dup"), _candidate("dup"), _candidate("uniq")],
        data_root=tmp_path,
    )
    assert sorted(c.slug for c in result.written) == ["dup", "uniq"]
    assert [c.slug for c in result.skipped_same_run] == ["dup"]
    assert result.reason(result.skipped_same_run[0]).endswith(", added in this run")


def test_markdown_summary_includes_counts_and_links(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    result = run(
        [
            _candidate("ok"),
            _candidate("draft", missing=("cores",)),
        ],
        data_root=tmp_path,
    )
    summary = result.markdown_summary()
    assert "written: **1**" in summary
    assert "skipped (repeats a row added in this run): 0" in summary
    assert "skipped (incomplete or uncertain): 1" in summary
    assert "## Added" in summary
    assert "cpu/intel/2024/consumer/ok.json" in summary
    assert "## Skipped (incomplete or uncertain)" in summary
    assert "missing: cores" in summary


def test_rows_sharing_a_slug_with_different_values_are_all_skipped(tmp_path: Path) -> None:
    gddr5 = _candidate("gt-1030")
    gddr5.record["memory_type"] = "GDDR5"
    ddr4 = _candidate("gt-1030")
    ddr4.record["memory_type"] = "DDR4"
    result = run([gddr5, ddr4], data_root=tmp_path, include_drafts=True)
    assert not result.written
    assert result.skipped_incomplete == [gddr5, ddr4]
    assert result.reason(ddr4) == (
        "uncertain: the source lists 2 rows named gt-1030 with different memory_type"
    )


def test_rows_sharing_a_slug_that_only_fill_different_fields_do_not_conflict(
    tmp_path: Path,
) -> None:
    first = _candidate("same")
    first.record["cores"] = 8
    second = _candidate("same")
    second.record["tdp_w"] = 65
    result = run([first, second], data_root=tmp_path)
    assert result.written == [first]
    assert result.skipped_same_run == [second]

