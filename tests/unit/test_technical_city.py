"""technical.city legacy-Cinebench source unit tests (no network)."""

from __future__ import annotations

from app.ingest.sources import technical_city as tc
from app.ingest.sources.technical_city import _field_for, _value, slug


def test_slug_drops_vendor_and_codename() -> None:
    assert slug("AMD Ryzen 7 5800X") == "Ryzen-7-5800X"
    assert slug("Intel Core i9-14900K") == "Core-i9-14900K"
    assert slug("Intel Core i7-2600K (Sandy Bridge)") == "Core-i7-2600K"
    assert slug("Intel Core 2 Duo E8400") == "Core-2-Duo-E8400"


def test_field_for_maps_versions() -> None:
    assert _field_for("Cinebench 15 64-bit single-core") == "cinebench_r15_single"
    assert _field_for("Cinebench 15 64-bit multi-core") == "cinebench_r15_multi"
    assert _field_for("Cinebench R10 32-bit single-core") == "cinebench_r10_single"
    assert _field_for("Cinebench 11.5 64-bit multi-core") == "cinebench_r11_5_multi"
    assert _field_for("Passmark") is None  # not a cinebench field
    assert _field_for("GeekBench 5 Single-Core") is None


def test_value_parses_int_and_decimal_and_ignores_trailing() -> None:
    assert _value("2,609", decimal=False) == 2609
    assert _value("27684Samples: 24208", decimal=False) == 27684  # trailing noise ignored
    assert _value("3.09", decimal=True) == 3.09


def test_fetch_legacy_parses_and_gates_on_heading() -> None:
    html = """
    <h1>Ryzen 7 5800X: specs and benchmarks</h1>
    <div class="tab"><h4>Cinebench 15 64-bit single-core</h4>
      <div class="rating-block"><div class="item"><div class="heading">
        <span class="title"><strong>Ryzen 7 5800X</strong></span>
        <em class="avarage">266</em></div></div></div></div>
    <div class="tab"><h4>Cinebench 15 64-bit multi-core</h4>
      <div class="rating-block"><div class="item"><div class="heading">
        <span class="title"><strong>Ryzen 7 5800X</strong></span>
        <em class="avarage">2609</em></div></div></div></div>
    <div class="tab"><h4>Cinebench 11.5 64-bit single-core</h4>
      <div class="rating-block"><div class="item"><div class="heading">
        <span class="title"><strong>Ryzen 7 5800X</strong></span>
        <em class="avarage">3.09</em></div></div></div></div>
    """

    class _Resp:
        status_code = 200
        text = html
        url = "https://technical.city/en/cpu/Ryzen-7-5800X"

    class _Client:
        def get(self, url):  # noqa: ANN001
            return _Resp()

    # vendor-insensitive match: dataset name carries "AMD", page heading doesn't.
    r = tc.fetch_legacy(_Client(), "AMD Ryzen 7 5800X")
    assert r is not None
    assert r.scores == {
        "cinebench_r15_single": 266,
        "cinebench_r15_multi": 2609,
        "cinebench_r11_5_single": 3.09,
    }

    # Wrong chip on the page → rejected (variant-safety).
    assert tc.fetch_legacy(_Client(), "AMD Ryzen 9 5950X") is None
