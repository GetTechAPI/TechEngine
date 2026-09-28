"""GPU benchmark sources — Blender (opendata) + Time Spy (topcpu). No network."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from app.ingest import enrich as enrich_mod
from app.ingest.sources import blender, topcpu, videocardbenchmark

# --- shared GPU name normalization (variant safety) ---------------------------


def test_normalize_gpu_matching_and_variants() -> None:
    n = blender.normalize_gpu
    # Vendor-prefixed source name collapses onto our vendorless dataset name.
    assert n("GeForce RTX 4070") == n("NVIDIA GeForce RTX 4070")
    assert n("Radeon RX 7900 XTX") == n("AMD Radeon RX 7900 XTX")
    assert n("Arc A770") == n("Intel Arc A770 Graphics")
    # Memory-size and OpenGL tails are dropped.
    assert n("Radeon RX 580 8GB") == n("AMD Radeon RX 580")
    assert n("GeForce RTX 3070/PCIe/SSE2") == n("GeForce RTX 3070")
    # Variants stay distinct.
    assert n("GeForce RTX 4070") != n("GeForce RTX 4070 Ti")
    assert n("GeForce RTX 4070 Ti") != n("GeForce RTX 4070 Ti Super")
    assert n("Radeon RX 7900 XT") != n("Radeon RX 7900 XTX")


# --- Blender (opendata snapshot) ----------------------------------------------


class _Resp:
    status_code = 200

    def __init__(self, content: bytes) -> None:
        self.content = content


class _ZipClient:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def get(self, url):  # noqa: ANN001
        return _Resp(self._content)


def _submission(device: str, version: str, spms: list[float]) -> dict:
    scenes = ["monster", "junkshop", "classroom"]
    return {
        "data": [
            {
                "blender_version": {"version": version},
                "device_info": {
                    "device_type": "OPTIX",
                    "compute_devices": [{"name": device, "type": "OPTIX"}],
                },
                "scene": {"label": scenes[i]},
                "stats": {"samples_per_minute": spm},
            }
            for i, spm in enumerate(spms)
        ]
    }


def _zip_of(lines: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("LICENSE.txt", "CC0")
        zf.writestr(
            "opendata-test.jsonl", "\n".join(json.dumps(x) for x in lines)
        )
    return buf.getvalue()


def test_blender_median_of_scene_sums_pinned_version() -> None:
    blender.reset_cache()
    name = "NVIDIA GeForce RTX 4080 SUPER"
    lines = [
        # Two 4.5 runs → sums 9000 and 8000 → median 8500.
        _submission(name, "4.5.0", [4500, 2300, 2200]),  # sum 9000
        _submission(name, "4.5.1", [4000, 2000, 2000]),  # sum 8000
        # A 3.6 run must be ignored (version pin).
        _submission(name, "3.6.0", [999, 999, 999]),
        # A CPU row must be ignored (only GPU device types count) — covered by
        # device_type filter; here we just add another version to be safe.
    ]
    out = blender.resolve(_ZipClient(_zip_of(lines)), "GeForce RTX 4080 Super")
    assert out is not None
    scores, url = out
    assert scores == {"blender_score": 8500.0}
    assert url == blender.SNAPSHOT_URL
    # Unknown GPU → None.
    assert blender.resolve(_ZipClient(_zip_of(lines)), "GeForce RTX 9999") is None


# --- Time Spy (topcpu ranking) ------------------------------------------------


class _HtmlResp:
    status_code = 200

    def __init__(self, text: str) -> None:
        self.text = text


class _HtmlClient:
    def __init__(self, text: str) -> None:
        self._text = text

    def get(self, url):  # noqa: ANN001
        return _HtmlResp(self._text)


TOPCPU_HTML = """
<div class="row">
  <input data-cmp value="GeForce RTX 4090">
  <span> 1. </span><a href="/en/cpu/x">NVIDIA GeForce RTX 4090</a>
  <span>24GB - 2022.09</span><span class="mx-2 grow"></span>
  <span class="text-sm font-bold ">36328</span>
</div>
<div class="row">
  <input data-cmp value="GeForce RTX 4070 Ti">
  <span> 2. </span><a href="/en/cpu/y">NVIDIA GeForce RTX 4070 Ti</a>
  <span>12GB</span><span class="font-bold">22000</span>
</div>
"""


def test_topcpu_parses_score_from_sibling_and_variant_safe() -> None:
    topcpu.reset_cache()
    client = _HtmlClient(TOPCPU_HTML)
    assert topcpu.resolve(client, "GeForce RTX 4090") == (
        {"timespy_score": 36328},
        topcpu.URL,
    )
    # Variant safety: plain 4070 absent here → None (only 4070 Ti present).
    assert topcpu.resolve(client, "GeForce RTX 4070") is None
    assert topcpu.resolve(client, "GeForce RTX 4070 Ti")[0]["timespy_score"] == 22000


def _cpu_row(name: str, score: str) -> str:
    # Real topcpu rows carry the full vendor-prefixed name in the input value.
    return (
        f'<div class="row"><input data-cmp value="{name}">'
        f'<a href="/x">{name}</a><span class="font-bold">{score}</span></div>'
    )


class _RoutingClient:
    """Serves different HTML per URL substring (CPU multi/single pages)."""

    def __init__(self, routes: dict[str, str]) -> None:
        self._routes = routes

    def get(self, url):  # noqa: ANN001
        for frag, html in self._routes.items():
            if frag in url:
                return _HtmlResp(html)
        return _HtmlResp("")


def test_topcpu_cpu_combines_multi_and_single_families() -> None:
    topcpu.reset_cache()
    n = "Intel Core i9-14900K"
    routes = {
        "cinebench-2024-multi-core": "<div>" + _cpu_row(n, "2130") + "</div>",
        "cinebench-2024-single-core": "<div>" + _cpu_row(n, "139") + "</div>",
        "passmark-cpu-multi-core": "<div>" + _cpu_row(n, "61120") + "</div>",
        "passmark-cpu-single-core": "<div>" + _cpu_row(n, "4770") + "</div>",
    }
    client = _RoutingClient(routes)
    out = topcpu.resolve_cpu(client, "Intel Core i9-14900K")
    assert out is not None
    scores, url = out
    assert scores == {
        "cinebench_2024_multi": 2130,
        "cinebench_2024_single": 139,
        "passmark_cpu_mark": 61120,
        "passmark_single": 4770,
    }
    assert url == topcpu.CPU_INDEX_URL
    # A CPU absent from every page → None.
    assert topcpu.resolve_cpu(client, "AMD Ryzen 5 9999X") is None


# --- PassMark GPU (videocardbenchmark) ----------------------------------------

VCB_HTML = """
<table>
  <tr id="gpu1"><td><a href="x">GeForce RTX 4090</a></td><td>38,073</td><td>5</td></tr>
  <tr id="gpu2"><td><a href="x">GeForce RTX 3070 Ti</a></td><td>23223</td><td>9</td></tr>
  <tr id="gpu3"><td><a href="x">GeForce 256</a></td><td>5</td><td>900</td></tr>
  <tr><td>header row no id</td><td>999</td></tr>
</table>
"""


def test_videocardbenchmark_parses_g3d_and_variant_safe() -> None:
    videocardbenchmark.reset_cache()
    client = _HtmlClient(VCB_HTML)
    # Comma-formatted score parsed; legacy card covered.
    assert videocardbenchmark.resolve(client, "GeForce RTX 4090") == (
        {"passmark_g3d_mark": 38073},
        videocardbenchmark.URL,
    )
    assert videocardbenchmark.resolve(client, "GeForce 256")[0]["passmark_g3d_mark"] == 5
    # Variant safety: plain 3070 absent (only 3070 Ti present) → None.
    assert videocardbenchmark.resolve(client, "GeForce RTX 3070") is None
    ti = videocardbenchmark.resolve(client, "GeForce RTX 3070 Ti")
    assert ti is not None
    assert ti[0]["passmark_g3d_mark"] == 23223


def _gpu_row(name: str, score: str) -> str:
    return (
        f'<div class="row"><input data-cmp value="{name}">'
        f'<a href="/x">{name}</a><span class="font-bold">{score}</span></div>'
    )


def test_topcpu_gpu_breadth_int_and_float() -> None:
    topcpu.reset_cache()
    n = "GeForce RTX 4090"
    routes = {
        "3dmark-time-spy-extreme": "<div>" + _gpu_row(n, "19460") + "</div>",
        "3dmark-speed-way": "<div>" + _gpu_row(n, "10074") + "</div>",
        "octanebench": "<div>" + _gpu_row(n, "1274") + "</div>",
        "fp32-float": "<div>" + _gpu_row(n, "82.58") + "</div>",  # float metric
    }
    out = topcpu.resolve_gpu(_RoutingClient(routes), "GeForce RTX 4090")
    assert out is not None
    scores, url = out
    assert scores == {
        "timespy_extreme_score": 19460,
        "speedway_score": 10074,
        "octanebench_score": 1274,
        "fp32_tflops": 82.58,  # parsed as float, not 8258
    }
    assert "gpu-r" in url
    assert topcpu.resolve_gpu(_RoutingClient(routes), "Radeon RX 9999") is None


@pytest.mark.parametrize("architecture", [
    "TeraScale", "TeraScale 2", "terascale 3", "Tesla", "Tesla 2.0",
    "Tesla 2.0 | Tesla", "Curie", "Rankine", "Kelvin", "Celsius",
    "Ultra-Threaded SE", "R100", "R200", "R300", "R400",
])
def test_topcpu_rejects_pre_dx12_time_spy(architecture: str) -> None:
    topcpu.reset_cache()
    client = _HtmlClient(_gpu_row("Legacy GPU", "3897"))
    assert topcpu.resolve(client, "Legacy GPU", architecture=architecture) is None


@pytest.mark.parametrize("architecture", [
    "Fermi", "Fermi 2.0", "Kepler", "Kepler 2.0", "GCN 1.0", "RDNA 2", "Ampere",
])
def test_topcpu_keeps_dx12_time_spy(architecture: str) -> None:
    topcpu.reset_cache()
    client = _HtmlClient(_gpu_row("Supported GPU", "1000"))
    assert topcpu.resolve(client, "Supported GPU", architecture=architecture) == (
        {"timespy_score": 1000}, topcpu.TIMESPY_URL,
    )


def test_topcpu_unknown_architecture_is_not_guessed() -> None:
    assert not topcpu.is_pre_dx12(None)
    assert not topcpu.is_pre_dx12("")
    assert not topcpu.is_pre_dx12("Unclassified")


@pytest.mark.parametrize("resolver,primary_field", [
    (topcpu.resolve, "timespy_score"), (topcpu.resolve_gpu, None),
])
def test_topcpu_enrichment_uses_architecture_not_year(
    tmp_path: Path, monkeypatch, resolver, primary_field,
) -> None:
    topcpu.reset_cache()
    # GCN and TeraScale coexisted in 2012; Fermi can run Time Spy despite 2010.
    records = [
        ("Radeon HD 7350", "TeraScale 2", "2012-01-01", False),
        ("Radeon HD 7970", "GCN 1.0", "2012-01-01", True),
        ("GeForce GTX 480", "Fermi", "2010-03-26", True),
        ("Tesla K20", "Kepler", "2012-11-12", True),
        ("GeForce GT 330", "Tesla 2.0", "2024-01-01", False),
    ]
    gpu_dir = tmp_path / "gpu"
    gpu_dir.mkdir()
    for i, (name, architecture, release_date, _) in enumerate(records):
        (gpu_dir / f"{i}.json").write_text(json.dumps({
            "name": name, "architecture": architecture, "release_date": release_date,
            "timespy_score": None, "timespy_extreme_score": None, "speedway_score": None,
            "octanebench_score": 55, "fp32_tflops": None, "source_urls": [],
        }), encoding="utf-8")

    def handle(request: httpx.Request) -> httpx.Response:
        value = "0.25" if "fp32-float" in str(request.url) else "1000"
        return httpx.Response(200, text="".join(_gpu_row(r[0], value) for r in records))

    monkeypatch.setattr(enrich_mod, "make_client", lambda: httpx.Client(
        transport=httpx.MockTransport(handle),
    ))
    result = enrich_mod.enrich(
        data_root=tmp_path, component="gpu", resolver=resolver,
        primary_field=primary_field, sleep=0,
    )
    for i, (_, _, _, supported) in enumerate(records):
        written = json.loads((gpu_dir / f"{i}.json").read_text(encoding="utf-8"))
        if resolver is topcpu.resolve:
            assert written["timespy_score"] == (1000 if supported else None)
        else:
            assert written["timespy_extreme_score"] == (1000 if supported else None)
            assert written["speedway_score"] == (1000 if supported else None)
            assert written["fp32_tflops"] == 0.25
        # Fill-only-nulls must still preserve existing data.
        assert written["octanebench_score"] == 55
    if resolver is topcpu.resolve:
        assert result.unresolved == ["Radeon HD 7350", "GeForce GT 330"]
    else:
        assert result.unresolved == []


def test_topcpu_pre_dx12_breadth_keeps_non_dx12_dimensions() -> None:
    topcpu.reset_cache()
    name = "GeForce GT 330"
    routes = {
        "3dmark-time-spy-extreme": _gpu_row(name, "2000"),
        "3dmark-speed-way": _gpu_row(name, "3000"),
        "octanebench": _gpu_row(name, "20"),
        "fp32-float": _gpu_row(name, "0.25"),
    }
    out = topcpu.resolve_gpu(_RoutingClient(routes), name, architecture="Tesla 2.0")
    assert out is not None
    assert out[0] == {"octanebench_score": 20, "fp32_tflops": 0.25}
