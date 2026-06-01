"""spec.org SPEC CPU2006 → specint2006 / specfp2006 (bulk result tables).

SPEC publishes every CINT2006 / CFP2006 *speed* result as one giant static
table (``cint2006.html`` / ``cfp2006.html``, ~11k rows each). Each row is a
single system submission; the processor sits in the final parenthesised group
of the "System Name" column (e.g. ``ACTINA SOLAR 220 X3 (Intel Xeon X5650)``,
sometimes with a ``, 2.30 GHz`` tail), and the last two cells are the Base and
Peak scores.

Like the cgdirector source these are *bulk tables*: each page is fetched once,
cached, and matched by exact normalized name (variant-safe — "i5-2400" never
matches "i5-2400S"). A chip appears in many submissions with differing scores
(different system / RAM / compiler); we keep the **maximum Base** result — the
best published baseline configuration, deterministic and verifiable from the
cited page. We use the *speed* metric (one copy), which is a per-CPU figure and
does not inflate with socket/core count the way the rate metric would.

SPEC CPU2006 was retired in 2018, so coverage is old desktop + server (Xeon,
Opteron, POWER) and stops before the 2017+ generation. Never fabricates.
"""

from __future__ import annotations

import re

import httpx

from .passmark import normalize_name

CINT_URL = "https://www.spec.org/cpu2006/results/cint2006.html"
CFP_URL = "https://www.spec.org/cpu2006/results/cfp2006.html"
# Both metrics are reachable from this canonical results index.
RESULTS_INDEX = "https://www.spec.org/cpu2006/results/"

# Strip a trailing clock annotation inside the processor parens, e.g.
# "Intel Xeon E5-2670 v3, 2.30 GHz" -> "Intel Xeon E5-2670 v3".
_CLOCK_TAIL = re.compile(r",\s*[\d.]+\s*[GM]Hz\s*$", re.IGNORECASE)
_PAREN = re.compile(r"\(([^()]*)\)")

_caches: dict[str, dict[str, float]] = {}


def _processor_from_system(system_name: str) -> str | None:
    """Extract the CPU model from a SPEC "System Name" cell.

    The processor is the last parenthesised group; drop a trailing ", X GHz".
    """
    groups = _PAREN.findall(system_name)
    if not groups:
        return None
    proc = _CLOCK_TAIL.sub("", groups[-1]).strip()
    return proc or None


def _load(client: httpx.Client, url: str) -> dict[str, float]:
    """Return ``{normalized_processor: max_base_score}`` for a results page."""
    if url in _caches:
        return _caches[url]
    table: dict[str, float] = {}
    _caches[url] = table
    resp = client.get(url)
    if resp.status_code != 200:
        return table
    # Stream-parse rows with a lightweight regex pass — bs4 on an 11k-row,
    # 8 MB document is needlessly slow and memory-hungry here.
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp.text, "html.parser")
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(cells) < 9:  # header / section rows have fewer / no <td>
            continue
        proc = _processor_from_system(cells[1])
        if not proc:
            continue
        try:
            base = float(cells[7])
        except (ValueError, IndexError):
            continue
        if base <= 0:
            continue
        key = normalize_name(proc)
        if not key:
            continue
        prev = table.get(key)
        if prev is None or base > prev:
            table[key] = base
    return table


def reset_cache() -> None:
    """Clear module caches (tests / re-runs)."""
    _caches.clear()


def resolve(
    client: httpx.Client, name: str, id_override: str | None = None
) -> tuple[dict[str, float], str] | None:
    """SPEC CPU2006 resolver: ``({specint2006?, specfp2006?}, url)`` or None."""
    key = normalize_name(name)
    if not key:
        return None
    scores: dict[str, float] = {}
    cint = _load(client, CINT_URL).get(key)
    if cint is not None:
        scores["specint2006"] = cint
    cfp = _load(client, CFP_URL).get(key)
    if cfp is not None:
        scores["specfp2006"] = cfp
    if not scores:
        return None
    return scores, RESULTS_INDEX
