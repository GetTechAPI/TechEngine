"""SPEC CPU2006 bulk-table source (specint2006 / specfp2006) — no network."""

from __future__ import annotations

from app.ingest.sources import spec2006


class _Resp:
    status_code = 200

    def __init__(self, text: str) -> None:
        self.text = text


class _Client:
    """Serves cint HTML for the CINT url, cfp HTML for the CFP url."""

    def __init__(self, cint: str, cfp: str) -> None:
        self._cint = cint
        self._cfp = cfp

    def get(self, url):  # noqa: ANN001
        return _Resp(self._cint if "cint" in url else self._cfp)


def _row(system: str, base: str, peak: str = "0") -> str:
    # 9 <td> cells: sponsor, system(+links), autopar, cores, chips, c/chip, t/core, base, peak.
    return (
        f"<tr><td>Sponsor</td><td>{system} HTML | CSV</td><td>Yes</td>"
        f"<td>4</td><td>1</td><td>4</td><td>1</td><td>{base}</td><td>{peak}</td></tr>"
    )


CINT = (
    "<table>"
    "<tr><th>Test Sponsor</th><th>System Name</th></tr>"
    # i5-2500K appears twice — keep the MAX base (47.4, not 40.0).
    + _row("Box A (Intel Core i5-2500K, 3.30 GHz)", "40.0")
    + _row("Box B (Intel Core i5-2500K)", "47.4", "56.4")
    # non-K sibling must stay distinct from the K SKU.
    + _row("Box C (Intel Core i5-2500)", "42.7")
    + _row("Server (AMD Opteron 6276)", "20.5")
    + "</table>"
)

CFP = (
    "<table>"
    "<tr><th>Test Sponsor</th><th>System Name</th></tr>"
    + _row("Box B (Intel Core i5-2500K)", "56.4")
    + "</table>"
)


def test_max_base_and_variant_safety() -> None:
    spec2006.reset_cache()
    client = _Client(CINT, CFP)
    # Keeps the maximum base across submissions; pulls fp from the other page.
    assert spec2006.resolve(client, "Intel Core i5-2500K") == (
        {"specint2006": 47.4, "specfp2006": 56.4},
        spec2006.RESULTS_INDEX,
    )
    # Non-K sibling resolves to its own row only (no fp data → int only).
    assert spec2006.resolve(client, "Intel Core i5-2500") == (
        {"specint2006": 42.7},
        spec2006.RESULTS_INDEX,
    )
    # Clock-suffixed paren still matches the plain name.
    assert spec2006.resolve(client, "AMD Opteron 6276")[0] == {"specint2006": 20.5}
    # Absent chip.
    assert spec2006.resolve(client, "AMD Ryzen 9 9999X") is None


def test_processor_extraction() -> None:
    f = spec2006._processor_from_system
    assert f("ACTINA 220 (Intel Xeon X5650) HTML | CSV") == "Intel Xeon X5650"
    assert f("Box (Intel Xeon E5-2670 v3, 2.30 GHz) Config") == "Intel Xeon E5-2670 v3"
    assert f("No parens here") is None
