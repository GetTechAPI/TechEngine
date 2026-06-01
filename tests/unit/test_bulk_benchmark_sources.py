"""Bulk-table benchmark sources (cgdirector R23, notebookcheck R15/R23) — no network."""

from __future__ import annotations

from app.ingest.sources import cgdirector, notebookcheck


class _Resp:
    status_code = 200

    def __init__(self, text: str) -> None:
        self.text = text


class _Client:
    def __init__(self, text: str) -> None:
        self._text = text

    def get(self, url):  # noqa: ANN001
        return _Resp(self._text)


CG_HTML = """
<table>
  <tr><th>CPU Name</th><th>Cores</th><th>Ghz</th><th>Single Score</th><th>Multi Score</th></tr>
  <tr><td>AMD Ryzen 7 5800X</td><td>8</td><td>4.7</td><td>1593</td><td>11201</td></tr>
  <tr><td>Intel Core i7 14700K</td><td>20</td><td>5.6</td><td>2228</td><td>33572</td></tr>
</table>
"""


def test_cgdirector_parses_and_matches_exact() -> None:
    cgdirector.reset_cache()
    client = _Client(CG_HTML)
    assert cgdirector.resolve(client, "AMD Ryzen 7 5800X") == (
        {"cinebench_r23_single": 1593, "cinebench_r23_multi": 11201},
        cgdirector.R23_URL,
    )
    # dash vs space in source name still matches
    out = cgdirector.resolve(client, "Intel Core i7-14700K")
    assert out and out[0]["cinebench_r23_multi"] == 33572
    # absent chip
    assert cgdirector.resolve(client, "AMD Ryzen 5 9999X") is None


CB2024_HTML = """
<table>
  <tr><th>CPU Name</th><th>Single Score</th><th>Multi Score</th></tr>
  <tr><td>AMD Ryzen 7 5800X</td><td>98</td><td>861</td></tr>
  <tr><td>Intel Core i9 14900K</td><td>139</td><td>2211</td></tr>
</table>
"""


def test_cgdirector_cinebench_2024() -> None:
    cgdirector.reset_cache()
    out = cgdirector.resolve_2024(_Client(CB2024_HTML), "AMD Ryzen 7 5800X")
    assert out == (
        {"cinebench_2024_single": 98, "cinebench_2024_multi": 861},
        cgdirector.CB2024_URL,
    )


NBC_HTML = """
<table>
  <tr><th>Model</th><th>Cores / Threads</th>
      <th>Cinebench R15 CPU Single 64Bit</th><th>Cinebench R15 CPU Multi 64Bit</th>
      <th>Cinebench R23 Single Core</th><th>Cinebench R23 Multi Core</th>
      <th>Geekbench 6.6 Multi-Core</th></tr>
  <tr><td>AMD Ryzen 7 5800X</td><td>8/16</td>
      <td>265.5 n2</td><td>2608.5 n2</td><td>1574.5 n2</td><td>15476 n2</td><td>10035</td></tr>
  <tr><td>Intel Core i7-1165G7</td><td>4/8</td>
      <td>218 n5</td><td>850 n5</td><td>1458 n5</td><td>5216 n5</td><td>5000</td></tr>
</table>
"""


def test_notebookcheck_extracts_r15_and_r23_only() -> None:
    notebookcheck.reset_cache()
    client = _Client(NBC_HTML)
    out = notebookcheck.resolve(client, "AMD Ryzen 7 5800X")
    assert out is not None
    scores, url = out
    assert url == notebookcheck.URL
    # R15 + R23 captured (rounded ints); Geekbench column NOT taken.
    assert scores == {
        "cinebench_r15_single": 266,
        "cinebench_r15_multi": 2608,
        "cinebench_r23_single": 1574,
        "cinebench_r23_multi": 15476,
    }
    assert notebookcheck.resolve(client, "Intel Core i7-1165G7")[0]["cinebench_r23_multi"] == 5216
    assert notebookcheck.resolve(client, "Nonexistent CPU 1") is None


def test_notebookcheck_geekbench_is_gb6_only() -> None:
    notebookcheck.reset_cache()
    out = notebookcheck.resolve_geekbench(_Client(NBC_HTML), "AMD Ryzen 7 5800X")
    # NBC_HTML carries only a GB6 multi column → GB5.x must never leak in.
    assert out is not None and out[0] == {"geekbench_multi": 10035}
