"""Ingest skips what it cannot get right: duplicates under other names, values
the source contradicts, architectures it would have to guess, unreleased parts."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.ingest.normalize import parse_date_period
from app.ingest.pipeline import run
from app.ingest.sources.base import IngestCandidate
from app.ingest.sources.wikipedia_cpu import WikipediaCpuIngest, architecture_from_heading

RUN_DATE = date(2026, 11, 2)


def _curate(root: Path, path: str, **record: object) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record), encoding="utf-8")


def _gpu(
    slug: str,
    manufacturer: str = "nvidia",
    *,
    clock: int = 500,
    tdp: int = 50,
    released: str = "2010-01-01",
) -> IngestCandidate:
    return IngestCandidate(
        category="gpu",
        manufacturer=manufacturer,
        slug=slug,
        record={
            "slug": slug,
            "name": slug,
            "manufacturer": manufacturer,
            "release_date": released,
            "base_clock_mhz": clock,
            "tdp_w": tdp,
        },
        source_url=f"https://example.test/{slug}",
        output_path=Path("gpu") / manufacturer / released[:4] / "consumer" / f"{slug}.json",
    )


@pytest.mark.parametrize(
    "candidate,curated",
    [
        # Another manufacturer folder: ATI and AMD share boards across the rebrand.
        (_gpu("radeon-hd-4580", "amd"), "gpu/ati/2011/consumer/radeon-hd-4580.json"),
        (_gpu("firepro-rg220", "ati"), "gpu/amd/2010/enterprise/firepro-rg220.json"),
        # Spelling only.
        (_gpu("geforce3-ti500"), "gpu/nvidia/2001/consumer/geforce3-ti-500.json"),
        # Name variants.
        (
            _gpu("geforce-fx-5700-e-s"),
            "gpu/nvidia/2003/consumer/geforce-fx-5700-engineering-sample.json",
        ),
        (_gpu("rtx-6000-ada-generation"), "gpu/nvidia/2022/enterprise/rtx-6000-ada.json"),
        (
            _gpu("rtx-pro-6000-blackwell-workstation-edition"),
            "gpu/nvidia/2025/enterprise/rtx-pro-6000-blackwell-workstation.json",
        ),
        (
            _gpu("rtx-pro-6000-blackwell-max-q-workstation-edition"),
            "gpu/nvidia/2025/enterprise/rtx-pro-6000-blackwell-max-q.json",
        ),
        (_gpu("quadro-t500-mobile"), "gpu/nvidia/2020/consumer/t500-mobile.json"),
        # Laptop-only parts listed without "Mobile", and one card per memory size.
        (_gpu("geforce-rtx-2050"), "gpu/nvidia/2021/consumer/geforce-rtx-2050-mobile.json"),
        (_gpu("geforce-gtx-1650-ti"), "gpu/nvidia/2020/consumer/geforce-gtx-1650-ti-mobile.json"),
        (_gpu("geforce-gtx-1060"), "gpu/nvidia/2016/consumer/geforce-gtx-1060-3gb.json"),
        # Bus and revision variants of a curated board.
        (_gpu("geforce-6800-gt-agp"), "gpu/nvidia/2004/consumer/geforce-6800-gt.json"),
        (_gpu("geforce4-mx-4000"), "gpu/nvidia/2003/consumer/geforce4-mx-4000-rev-2.json"),
        (_gpu("radeon-x1550", "ati"), "gpu/ati/2007/consumer/radeon-x1550-agp.json"),
    ],
)
def test_gpu_already_curated_under_another_name_is_skipped(
    candidate: IngestCandidate, curated: str, tmp_path: Path
) -> None:
    _curate(tmp_path, curated, slug=Path(curated).stem)
    result = run([candidate], data_root=tmp_path, run_date=RUN_DATE)
    assert not result.written
    assert result.skipped_existing == [candidate]
    assert curated in result.reason(candidate)


def test_gpu_name_that_extends_a_curated_board_with_its_specs_is_skipped(tmp_path: Path) -> None:
    # HD 2350 vs the curated HD 2350 PRO: same clock and TDP, so likely one board.
    _curate(
        tmp_path,
        "gpu/ati/2007/consumer/radeon-hd-2350-pro.json",
        slug="radeon-hd-2350-pro",
        base_clock_mhz=525,
        tdp_w=20,
    )
    twin = _gpu("radeon-hd-2350", "ati", clock=525, tdp=20, released="2007-06-28")
    result = run([twin], data_root=tmp_path, run_date=RUN_DATE)
    assert result.skipped_existing == [twin]
    assert "same clock and TDP" in result.reason(twin)


def test_gpu_distinct_tier_is_still_added(tmp_path: Path) -> None:
    _curate(
        tmp_path,
        "gpu/nvidia/2003/consumer/geforce-fx-5700.json",
        slug="geforce-fx-5700",
        base_clock_mhz=425,
        tdp_w=20,
    )
    ultra = _gpu("geforce-fx-5700-ultra", clock=475, tdp=30, released="2003-10-23")
    result = run([ultra], data_root=tmp_path, run_date=RUN_DATE)
    assert result.written == [ultra]


def test_dedup_reads_data_root_not_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(elsewhere))
    root = tmp_path / "data"
    _curate(root, "gpu/ati/2011/consumer/radeon-hd-4580.json", slug="radeon-hd-4580")
    result = run([_gpu("radeon-hd-4580", "amd")], data_root=root, run_date=RUN_DATE)
    assert not result.written


def test_existing_path_is_never_written_over(tmp_path: Path) -> None:
    candidate = _gpu("brand-new-board")
    _curate(tmp_path, candidate.output_path.as_posix(), slug="something-else")
    result = run([candidate], data_root=tmp_path, run_date=RUN_DATE)
    assert not result.written
    assert "already exists" in result.reason(candidate)
    on_disk = json.loads((tmp_path / candidate.output_path).read_text(encoding="utf-8"))
    assert on_disk == {"slug": "something-else"}


def test_smartphone_dedup_spans_brand_folders(tmp_path: Path) -> None:
    _curate(tmp_path, "smartphone/samsung/2015/galaxy-note5.json", slug="galaxy-note5")
    phone = IngestCandidate(
        category="smartphone",
        manufacturer="samsung",
        slug="galaxy-note-5",
        record={"slug": "galaxy-note-5", "release_date": "2015-08-21"},
        source_url="https://example.test/note5",
        output_path=Path("smartphone/samsung/galaxy-note-5.json"),
    )
    result = run([phone], data_root=tmp_path, run_date=RUN_DATE)
    assert result.skipped_existing == [phone]


# --- CPU ------------------------------------------------------------------------

_HYBRID_HTML = """
<html><body>
<h2>Mobile</h2>
<h3>Arrow Lake</h3>
<table class="wikitable">
  <tr>
    <th rowspan="2">Processor Family</th><th rowspan="2">Model</th>
    <th colspan="4">Cores (threads)</th><th colspan="2">Arc Graphics</th>
    <th colspan="2">TDP</th><th rowspan="2">Released</th>
  </tr>
  <tr>
    <th>P</th><th>E</th><th>LP-E</th><th>Total</th>
    <th>X Cores (VE : RT)</th><th>Clock (GHz)</th><th>Base</th><th>Turbo</th>
  </tr>
  <tr>
    <td>Core Ultra 7</td><td>265U</td><td>2 (4)</td><td>8 (8)</td><td>2 (2)</td><td>12 (14)</td>
    <td>4 (64:4)</td><td>2.1</td><td>15 W</td><td>57 W</td><td>Jan 6, 2025</td>
  </tr>
  <tr>
    <td>Core Ultra 7</td><td>165HL</td><td>6 (12)</td><td>8 (8)</td><td>2 (2)</td><td>16 (24)</td>
    <td>8 (128:8)</td><td>2.3</td><td>45 W</td><td>115 W</td><td>Q2 2024</td>
  </tr>
</table>
</body></html>
"""


def _hybrid() -> dict[str, IngestCandidate]:
    candidates = WikipediaCpuIngest._extract(
        _HYBRID_HTML, "intel", "List_of_Intel_Core_processors", "Intel Core"
    )
    return {c.slug: c for c in candidates}


def test_hybrid_cpu_counts_every_core_type() -> None:
    chip = _hybrid()["core-ultra-7-265u"]
    assert (chip.record["cores"], chip.record["threads"]) == (12, 14)
    assert chip.record["tdp_w"] == 15  # the TDP column, not a clock
    assert not chip.problems


def test_hybrid_cpu_whose_total_contradicts_its_parts_is_skipped(tmp_path: Path) -> None:
    chip = _hybrid()["core-ultra-7-165hl"]
    assert chip.record["cores"] is None
    assert chip.problems == ("P/E/LP-E cores add up to 16 (22) but the Total column says 16 (24)",)
    result = run([chip], data_root=tmp_path, include_drafts=True, run_date=RUN_DATE)
    assert not result.written
    assert result.reason(chip).startswith("uncertain: ")


@pytest.mark.parametrize(
    "heading,expected",
    [
        ("Mobile", ""),
        ("Mobile processors", ""),
        ("Desktop", ""),
        ("IoT/Embedded", ""),
        ("Sixth generation Epyc (Venice and Venice Dense)", ""),
        ("Desktop (codenamed Kaby Lake and Skylake-X )", ""),
        ("Desktop (codenamed Comet Lake )", "Comet Lake"),
        ("Raphael (7000 series, Zen 4/RDNA2 based)", "Zen 4"),
        ('" Denverton " (14 nm)', "Denverton"),
        ("Raptor Lake (13th gen)", "Raptor Lake"),
    ],
)
def test_architecture_is_never_a_market_heading(heading: str, expected: str) -> None:
    assert architecture_from_heading(heading) == expected


def test_cpu_under_a_market_heading_misses_its_architecture() -> None:
    html = _HYBRID_HTML.replace("<h3>Arrow Lake</h3>", "")
    page = "List_of_Intel_Core_processors"
    chip = {
        c.slug: c for c in WikipediaCpuIngest._extract(html, "intel", page, "Intel Core")
    }["core-ultra-7-265u"]
    assert chip.record["architecture"] == ""
    assert "architecture" in chip.missing_fields


def test_part_not_released_by_the_run_date_is_skipped(tmp_path: Path) -> None:
    epyc = IngestCandidate(
        category="cpu",
        manufacturer="amd",
        slug="epyc-9586f",
        record={"slug": "epyc-9586f", "release_date": "2026-10-01"},
        source_url="https://example.test/epyc",
        output_path=Path("cpu/amd/2026/enterprise/epyc-9586f.json"),
        released_by=date(2026, 12, 31),  # "Q4 2026"
    )
    future = IngestCandidate(
        category="cpu",
        manufacturer="amd",
        slug="epyc-9016",
        record={"slug": "epyc-9016", "release_date": "2027-01-01"},
        source_url="https://example.test/epyc",
        output_path=Path("cpu/amd/2027/enterprise/epyc-9016.json"),
    )
    result = run([epyc, future], data_root=tmp_path, include_drafts=True, run_date=RUN_DATE)
    assert not result.written
    assert all(result.reason(c).startswith("not released by 2026-11-02") for c in (epyc, future))


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Q4 2026", (date(2026, 10, 1), date(2026, 12, 31))),
        ("Dec 2024", (date(2024, 12, 1), date(2024, 12, 31))),
        ("2027", (date(2027, 1, 1), date(2027, 12, 31))),
        ("Jan 6, 2025", (date(2025, 1, 6), date(2025, 1, 6))),
        ("", None),
    ],
)
def test_parse_date_period(text: str, expected: tuple[date, date] | None) -> None:
    assert parse_date_period(text) == expected
