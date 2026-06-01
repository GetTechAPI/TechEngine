"""PassMark scraper variant-safety + enrichment unit tests (no network)."""

from __future__ import annotations

import json
from pathlib import Path

from app.ingest import enrich as enrich_mod
from app.ingest.sources import passmark
from app.ingest.sources.passmark import (
    PassMarkResult,
    _extract,
    heading_matches,
    normalize_name,
)


def test_normalize_strips_clock_and_graphics_tails() -> None:
    assert normalize_name("AMD Ryzen 7 5800X @ 3.80GHz") == normalize_name(
        "AMD Ryzen 7 5800X"
    )
    assert normalize_name("AMD Ryzen 5 4600G with Radeon Graphics") == normalize_name(
        "AMD Ryzen 5 4600G"
    )
    assert normalize_name("Intel Celeron G5905 (Comet Lake)") == normalize_name(
        "Intel Celeron G5905"
    )


def test_variants_stay_distinct() -> None:
    # The whole point: fuzzy siblings must NOT compare equal.
    assert not heading_matches("AMD Ryzen 7 5800X", "AMD Ryzen 7 5800X3D")
    assert not heading_matches("Intel Core i9-14900K", "Intel Core i9-14900KS")
    assert not heading_matches("Intel Core i5-12400", "Intel Core i5-12400F")
    assert not heading_matches("AMD Ryzen 9 5900X", "AMD Ryzen 9 5900XT")
    # ...but a clock-suffixed exact match must.
    assert heading_matches("Intel Core i9-13900K", "Intel Core i9-13900K @ 3.00GHz")


def test_extract_reads_labels() -> None:
    html = """
    <html><body>
      <span class="cpuname">AMD Ryzen 7 5800X @ 3.80GHz</span>
      <div>Multithread Rating: 27,684</div>
      <div>Single Thread Rating: 3,448</div>
    </body></html>
    """
    parsed = _extract(html)
    assert parsed is not None
    heading, mark, single = parsed
    assert heading.startswith("AMD Ryzen 7 5800X")
    assert (mark, single) == (27684, 3448)


class _FakeResp:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class _FakeClient:
    """Returns a canned lookup-results page for resolve_id parsing."""

    def __init__(self, text: str) -> None:
        self._text = text

    def get(self, url, params=None):  # noqa: ANN001
        return _FakeResp(self._text)


def test_resolve_id_picks_exact_variant() -> None:
    # Lookup list with several i5-2500 siblings; only the plain one must win.
    html = """
    <a href="/cpu.php?cpu=Intel+Core+i5-2500K&id=804">
      <span class="prdname">Intel Core i5-2500K @ 3.30GHz</span></a>
    <a href="/cpu.php?cpu=Intel+Core+i5-2500&id=803">
      <span class="prdname">Intel Core i5-2500 @ 3.30GHz</span></a>
    <a href="/cpu.php?cpu=Intel+Core+i5-2500S&id=805">
      <span class="prdname">Intel Core i5-2500S @ 2.70GHz</span></a>
    """
    assert passmark.resolve_id(_FakeClient(html), "Intel Core i5-2500") == "803"
    assert passmark.resolve_id(_FakeClient(html), "Intel Core i5-2500K") == "804"
    assert passmark.resolve_id(_FakeClient(html), "Intel Core i5-9999") is None


def test_enrich_fills_only_exact_match_nulls(tmp_path: Path, monkeypatch) -> None:
    cpu_dir = tmp_path / "cpu" / "amd" / "2020" / "consumer"
    cpu_dir.mkdir(parents=True)
    rec = {
        "slug": "ryzen-7-5800x",
        "name": "AMD Ryzen 7 5800X",
        "passmark_single": None,
        "passmark_cpu_mark": None,
        "source_urls": ["https://amd.com/x"],
    }
    path = cpu_dir / "ryzen-7-5800x.json"
    path.write_text(json.dumps(rec), encoding="utf-8")

    def fake_fetch(client, name, *, id_override=None):  # noqa: ANN001
        return PassMarkResult("AMD Ryzen 7 5800X", 27684, 3448, "https://cpubenchmark.net/x")

    monkeypatch.setattr(enrich_mod, "fetch_scores", fake_fetch)
    monkeypatch.setattr(passmark, "make_client", lambda **k: None)
    monkeypatch.setattr(enrich_mod, "make_client", lambda **k: None)

    result = enrich_mod.enrich(data_root=tmp_path, sleep=0)

    assert len(result.filled) == 1
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["passmark_single"] == 3448
    assert written["passmark_cpu_mark"] == 27684
    assert "https://cpubenchmark.net/x" in written["source_urls"]


def test_enrich_reports_unresolved_on_mismatch(tmp_path: Path, monkeypatch) -> None:
    cpu_dir = tmp_path / "cpu" / "intel" / "2024" / "consumer"
    cpu_dir.mkdir(parents=True)
    path = cpu_dir / "core-i5-12400.json"
    path.write_text(
        json.dumps(
            {"slug": "core-i5-12400", "name": "Intel Core i5-12400",
             "passmark_single": None, "passmark_cpu_mark": None, "source_urls": []}
        ),
        encoding="utf-8",
    )
    # Simulate fuzzy mismatch → client returns None.
    monkeypatch.setattr(enrich_mod, "fetch_scores", lambda *a, **k: None)
    monkeypatch.setattr(enrich_mod, "make_client", lambda **k: None)

    result = enrich_mod.enrich(data_root=tmp_path, sleep=0)

    assert result.filled == []
    assert "Intel Core i5-12400" in result.unresolved
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["passmark_cpu_mark"] is None  # untouched
