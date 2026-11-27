"""Tier 2 — compare a record with the Wikipedia table row it cites.

Most hardware records cite the list article they were taken from (``List of AMD
K5 processors``, ``CPU socket``). That the article is alive says nothing about a
record's values, and a label search on Wikidata finds no entity for a single SKU.
This check reads the cited article once, finds the row whose name cell is the
record's name, and compares the fields both sides carry.

* the name must match exactly after normalization (``+`` kept as ``plus``, the
  maker prefix and the category's class word folded on both sides)
* a confirm needs :data:`MIN_AGREEMENTS` agreeing fields and no conflict
* any conflicting field on every matching row is a ``contradict`` — the record
  must not be promoted, and the conflict is logged for correction
* a row that matches by name but carries too few comparable fields is
  ``insufficient``, never a confirm

A confirm means "the record states what the cited revision states". The revision
id is stored with the decision so the evidence can be re-read later.

Network access goes through an injected fetcher; the comparison is pure.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, Tag

from app.ingest.normalize import parse_date
from app.ingest.sources.wikitable import enclosing_headings, parse_table_header_block

from .common import STATE_DIR

CACHE_PATH = STATE_DIR / "rowcheck_cache.jsonl"
USER_AGENT = "TechEngine/0.1 (https://github.com/GetTechAPI/TechEngine; source verification)"
API = "https://en.wikipedia.org/w/api.php"

CONFIRM = "confirm"
CONTRADICT = "contradict"
INSUFFICIENT = "insufficient"
NOTFOUND = "notfound"
NOPAGE = "nopage"  # the citation is not an English Wikipedia article we can read

MIN_AGREEMENTS = 2

_NORM_RE = re.compile(r"[^a-z0-9]+")
_FOOTNOTE_RE = re.compile(r"\[[^\]]*\]")
_PAREN_RE = re.compile(r"\(([^)]*)\)")
_NUMBER_RE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)*)")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d|s)")
# 'LGA 1150 / Socket H3' names one row twice; '946B/947' is one spelling.
_ALT_NAME_RE = re.compile(r"\s+/\s*|\s*/\s+")


def normalize_name(text: str) -> str:
    """Lowercase alphanumerics, with ``+`` kept: 'AM2+' and 'AM2' must differ."""
    return _NORM_RE.sub("", text.lower().replace("+", " plus "))


def _word_key(text: str) -> str:
    """The same words in any order: 'Ryzen 3 PRO 1200' is 'Ryzen 3 1200 PRO'."""
    words = sorted(w for w in _NORM_RE.split(text.lower().replace("+", " plus ")) if w)
    return "words:" + " ".join(words) if len(words) > 1 else ""


_TITLE_NOISE = frozenset({
    "list", "of", "comparison", "processors", "processor", "microprocessors",
    "microprocessor", "chipsets", "chipset", "series", "cpu", "cpus", "and",
    "graphics", "processing", "units", "unit", "system", "systems", "on", "chip", "chips",
})


def title_family(title: str, makers: Iterable[str] = ()) -> str:
    """The product family an article is about: 'List of AMD Opteron processors'
    -> 'Opteron'. Its tables then name rows by the model alone ('140')."""
    drop = _TITLE_NOISE | {m.lower() for m in makers}
    words = _PAREN_RE.sub(" ", title.replace("_", " ")).split()
    return " ".join(w for w in words if w.lower() not in drop)


# --- which article a record cites ------------------------------------------------


@dataclass(frozen=True)
class PageRef:
    title: str
    oldid: int | None = None  # a pinned revision, when the citation carries one

    @property
    def key(self) -> str:
        return f"{self.title}@{self.oldid}" if self.oldid else self.title


def page_ref(url: Any) -> PageRef | None:
    """The article (and pinned revision) an English Wikipedia URL points at."""
    if not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != "en.wikipedia.org":
        return None
    if parsed.path.startswith("/wiki/"):
        title = unquote(parsed.path[len("/wiki/"):])
        return PageRef(title.replace(" ", "_")) if title else None
    if parsed.path == "/w/index.php":
        query = parse_qs(parsed.query)
        title = (query.get("title") or [""])[0]
        oldid = (query.get("oldid") or [""])[0]
        if title:
            return PageRef(title.replace(" ", "_"), int(oldid) if oldid.isdigit() else None)
    return None


# --- the article's tables --------------------------------------------------------


@dataclass(frozen=True)
class Table:
    headings: tuple[str, ...]  # innermost section first
    labels: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Page:
    ref: PageRef
    revid: int
    tables: tuple[Table, ...]


def _clean(text: str) -> str:
    return " ".join(_FOOTNOTE_RE.sub("", text.replace("\xa0", " ")).split())


def tables_from_html(html: str) -> list[Table]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[Table] = []
    for table in soup.select("table.wikitable"):
        if not isinstance(table, Tag):
            continue
        headings = tuple(enclosing_headings(table))
        labels, body = parse_table_header_block(table)
        if not labels or not body:
            continue
        out.append(Table(
            headings,
            tuple(_clean(label) for label in labels),
            tuple(tuple(_clean(cell) for cell in row) for row in body),
        ))
    return out


# --- field comparison ------------------------------------------------------------

AGREE, CONFLICT, NA = "agree", "conflict", "na"
Compare = Callable[[Any, str, str], str]


def _numbers(text: str) -> list[float]:
    out = []
    for raw in _NUMBER_RE.findall(text):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.005, 0.01 * abs(b))


def _unknown(cell: str) -> bool:
    return not cell or cell.strip(" ?-–—") == "" or cell.lower() in {"n/a", "unknown", "tba"}


def cmp_release(value: Any, cell: str, _label: str) -> str:
    """A full date must match to the day; otherwise the year must be named.

    A record date on the first of a month is a month (or, in January, a year)
    written out, so it is compared at that precision only."""
    if not isinstance(value, str) or len(value) < 4 or not value[:4].isdigit():
        return NA
    years = {int(y) for y in _YEAR_RE.findall(cell)}
    if not years:
        return NA
    if len(value) >= 10 and len(years) == 1 and not value[:10].endswith("-01"):
        exact = parse_date(cell)
        if exact is not None and exact.day != 1:
            return AGREE if exact.isoformat() == value[:10] else CONFLICT
    return AGREE if int(value[:4]) in years else CONFLICT


def cmp_int(value: Any, cell: str, _label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return NA
    nums = _numbers(cell)
    if not nums:
        return NA
    return AGREE if any(n == float(value) for n in nums) else CONFLICT


def _cmp_unit(units: dict[str, float], label_units: dict[str, float]) -> Compare:
    """Numbers followed by a unit (or under a label naming one), scaled to the
    record's unit. A bare number with no unit anywhere is not comparable."""
    unit_re = re.compile(
        r"(\d+(?:[.,]\d+)*)\s*(" + "|".join(sorted(units, key=len, reverse=True)) + r")\b",
        re.IGNORECASE,
    )

    def compare(value: Any, cell: str, label: str) -> str:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return NA
        found = [
            float(num.replace(",", "")) * units[unit.lower()]
            for num, unit in unit_re.findall(cell)
        ]
        if not found:
            scale = next((s for u, s in label_units.items() if u in label.lower()), None)
            if scale is None:
                return NA
            found = [n * scale for n in _numbers(cell)]
        if not found:
            return NA
        return AGREE if any(_close(n, float(value)) for n in found) else CONFLICT

    return compare


cmp_ghz = _cmp_unit({"ghz": 1.0, "mhz": 0.001}, {"ghz": 1.0, "mhz": 0.001})
cmp_watt = _cmp_unit({"w": 1.0}, {"(w)": 1.0, "watt": 1.0})
cmp_nm = _cmp_unit({"nm": 1.0, "μm": 1000.0, "µm": 1000.0}, {"nm": 1.0})
cmp_cache_mb = _cmp_unit(
    {"mb": 1.0, "mib": 1.0, "kb": 1 / 1024, "kib": 1 / 1024}, {"(mb)": 1.0, "(mib)": 1.0}
)


cmp_memory_gb = _cmp_unit(
    {"gb": 1.0, "gib": 1.0, "tb": 1024.0, "mb": 1 / 1024, "mib": 1 / 1024},
    {"(gb)": 1.0, "(gib)": 1.0, "(mb)": 1 / 1024, "(mib)": 1 / 1024},
)
cmp_mhz = _cmp_unit({"mhz": 1.0, "ghz": 1000.0}, {"mhz": 1.0, "ghz": 1000.0})


def cmp_price(value: Any, cell: str, _label: str) -> str:
    if "$" not in cell and "usd" not in _label.lower():
        return NA
    return cmp_int(value, cell.replace("$", " "), _label)


def cmp_text(value: Any, cell: str, _label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return NA
    want, have = normalize_name(value), normalize_name(cell)
    if not want or not have:
        return NA
    return AGREE if want == have or want in have else CONFLICT


@dataclass(frozen=True)
class FieldSpec:
    name: str  # record field
    label: re.Pattern[str]  # column labels that carry it
    compare: Compare
    reject: re.Pattern[str] | None = None  # labels that only look like it
    soft: bool = False  # free text: may agree, a mismatch proves nothing


def _spec(
    name: str, label: str, compare: Compare, reject: str | None = None, *, soft: bool = False
) -> FieldSpec:
    return FieldSpec(
        name, re.compile(label, re.IGNORECASE), compare,
        re.compile(reject, re.IGNORECASE) if reject else None, soft,
    )


def _field(rec: dict[str, Any], path: str) -> Any:
    cur: Any = rec
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


_RELEASE = _spec(
    "release_date", r"release|launch|introduc|\bdate\b|\byear\b", cmp_release,
    r"price|discontinu|end of|eol",
)

# Order matters: a column goes to the first spec whose label matches.
FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    "socket": (
        _RELEASE,
        _spec("package_type", r"^package", cmp_text),
        _spec("pin_count", r"pin count|\bpins\b|contacts", cmp_int),
    ),
    "cpu": (
        _spec("msrp_usd", r"price", cmp_price),
        _RELEASE,
        _spec("tdp_w", r"\btdp\b|thermal design", cmp_watt),
        _spec("socket", r"socket", cmp_text),
        _spec("l3_cache_mb", r"\bl3\b", cmp_cache_mb),
        _spec("boost_clock_ghz", r"turbo|boost", cmp_ghz),
        _spec("base_clock_ghz", r"frequency|clock", cmp_ghz,
              r"fsb|bus|gpu|graphics|memory|multi|turbo|boost"),
        _spec("threads", r"^threads", cmp_int),
        _spec("cores", r"^cores", cmp_int, r"thread"),
    ),
    "gpu": (
        _spec("msrp_usd", r"price", cmp_price),
        _RELEASE,
        _spec("tdp_w", r"\btdp\b|thermal design|\bpower\b", cmp_watt),
        _spec("memory_bus_bit", r"bus width", cmp_int),
        _spec("memory_type", r"memory.*type|bus type|ram type", cmp_text, soft=True),
        _spec("memory_gb", r"memory.*size|size \((mib|gib|mb|gb)\)", cmp_memory_gb),
        _spec("base_clock_mhz", r"core.*(clock|mhz)|clock.*core", cmp_mhz,
              r"memory|boost|turbo|shader"),
    ),
    "soc": (
        _RELEASE,
        _spec("process_nm", r"\bfab|\bprocess\b|\bnode\b|technology|lithograph", cmp_nm),
        _spec("gpu_name", r"\bgpu\b|graphics", cmp_text, soft=True),
    ),
    "chipset": (
        _RELEASE,
        _spec("process_nm", r"\bfab|\bprocess\b|\bnm\b|lithograph", cmp_nm),
        _spec("tdp_w", r"\btdp\b|power", cmp_watt),
        _spec("max_memory_gb", r"max.*(memory|ram)|(memory|ram).*max", cmp_memory_gb),
    ),
}

# Columns that name the row. The first column is the fallback.
NAME_LABELS: dict[str, re.Pattern[str]] = {
    "socket": re.compile(r"socket name|^socket$|^name", re.IGNORECASE),
    "cpu": re.compile(r"model|^name|processor", re.IGNORECASE),
    "chipset": re.compile(r"chipset|model|^name|code ?name", re.IGNORECASE),
    "gpu": re.compile(r"model|^name", re.IGNORECASE),
    "soc": re.compile(r"model|^name|^soc|part number|product", re.IGNORECASE),
}

# 'Integrated GPU / Model' and 'Socket / Model' name something else in the row.
_NOT_NAME_RE = re.compile(r"gpu|graphics|socket /|chipset /|southbridge|modem", re.IGNORECASE)

# A leading word both sides may or may not write: 'Socket AM4' vs 'AM4'.
CLASS_WORDS: dict[str, tuple[str, ...]] = {"socket": ("socket",), "chipset": ("chipset",)}

# Categories whose ``series`` is the section the row sits under. Agreement places
# the record in the right table; it is never a conflict, sections get renamed.
SECTION_FIELD: dict[str, str] = {"chipset": "series"}


def name_keys(text: str, category: str, makers: Iterable[str] = ()) -> set[str]:
    """Normalized spellings under which ``text`` may be matched exactly."""
    text = _clean(text)
    variants = {text, _PAREN_RE.sub(" ", text)}
    variants.update(m.strip() for m in _PAREN_RE.findall(text))
    variants.update(part.strip() for part in _ALT_NAME_RE.split(text))
    keys: set[str] = set()
    for variant in variants:
        words = variant.split()
        classes = set(CLASS_WORDS.get(category, ()))
        lead = {m.lower() for m in makers} | classes
        keys.update((normalize_name(variant), _word_key(variant)))
        while words and (words[0].lower() in lead or words[-1].lower() in classes):
            words = words[1:] if words[0].lower() in lead else words[:-1]
            keys.update((normalize_name(" ".join(words)), _word_key(" ".join(words))))
    return {k for k in keys if k}


def row_names(cells: list[str], family: str) -> list[str]:
    """Spellings a row answers to: each name cell, the name cells read together
    ('Core i7' + '4860EQ'), and either one under the article's family."""
    cells = [c for c in cells if c]
    names = list(cells)
    for end in range(2, len(cells) + 1):
        names.append(" ".join(dict.fromkeys(cells[:end])))
    if family:
        names.extend(f"{family} {name}" for name in list(names))
    return names


@dataclass(frozen=True)
class RowMatch:
    table: int
    headings: tuple[str, ...]
    agreements: tuple[str, ...]
    conflicts: tuple[tuple[str, str, str], ...]  # (field, record value, cell)


@dataclass(frozen=True)
class Decision:
    decision: str
    rows_matched: int = 0
    agreements: tuple[str, ...] = ()
    conflicts: tuple[tuple[str, str, str], ...] = ()
    headings: tuple[str, ...] = ()
    comparable: frozenset[str] = field(default_factory=frozenset)


def _columns(table: Table, category: str) -> tuple[list[int], dict[int, FieldSpec]]:
    name_re = NAME_LABELS.get(category)
    names = [
        i for i, label in enumerate(table.labels)
        if name_re and name_re.search(label) and not _NOT_NAME_RE.search(label)
    ]
    fields: dict[int, FieldSpec] = {}
    for i, label in enumerate(table.labels):
        if i in names:
            continue
        for spec in FIELDS.get(category, ()):
            if spec.label.search(label) and not (spec.reject and spec.reject.search(label)):
                fields[i] = spec
                break
    return names or [0], fields


def match_rows(rec: dict[str, Any], category: str, page: Page) -> list[RowMatch]:
    makers = [m for m in (rec.get("manufacturer"), rec.get("brand")) if isinstance(m, str)]
    wanted: set[str] = set()
    for name in [rec.get("name"), *(rec.get("aliases") or [])]:
        if isinstance(name, str):
            wanted |= name_keys(name, category, makers)
    out: list[RowMatch] = []
    family = title_family(page.ref.title, makers)
    for t_idx, table in enumerate(page.tables):
        name_cols, field_cols = _columns(table, category)
        for row in table.rows:
            names = row_names([row[i] for i in name_cols if i < len(row)], family)
            if not any(wanted & name_keys(name, category, makers) for name in names):
                continue
            results: dict[str, tuple[str, str]] = {}
            for i, spec in field_cols.items():
                if i >= len(row) or _unknown(row[i]):
                    continue
                verdict = spec.compare(_field(rec, spec.name), row[i], table.labels[i])
                if spec.soft and verdict == CONFLICT:
                    continue
                # Two columns for one field (base and max frequency): one agreeing
                # column is enough, a conflict stands only if none agrees.
                if verdict != NA and results.get(spec.name, ("", ""))[0] != AGREE:
                    results[spec.name] = (verdict, row[i])
            section = rec.get(SECTION_FIELD.get(category, ""))
            if isinstance(section, str) and normalize_name(section) in {
                normalize_name(h) for h in table.headings
            }:
                results[SECTION_FIELD[category]] = (AGREE, section)
            out.append(RowMatch(
                t_idx, table.headings,
                tuple(sorted(f for f, (v, _c) in results.items() if v == AGREE)),
                tuple(sorted(
                    (f, str(_field(rec, f)), cell)
                    for f, (v, cell) in results.items() if v == CONFLICT
                )),
            ))
    return out


def check_record(rec: dict[str, Any], category: str, page: Page) -> Decision:
    rows = match_rows(rec, category, page)
    if not rows:
        return Decision(NOTFOUND)
    clean = [r for r in rows if not r.conflicts]
    best = max(clean, key=lambda r: len(r.agreements), default=None)
    if best is not None and len(best.agreements) >= MIN_AGREEMENTS:
        return Decision(CONFIRM, len(rows), best.agreements, (), best.headings)
    if best is None:
        # Every row with this name disagrees with the record somewhere.
        worst = min(rows, key=lambda r: (len(r.conflicts), -len(r.agreements)))
        return Decision(CONTRADICT, len(rows), worst.agreements, worst.conflicts, worst.headings)
    return Decision(INSUFFICIENT, len(rows), best.agreements, (), best.headings)


# --- fetching --------------------------------------------------------------------


def fetch_page(ref: PageRef, *, timeout: float = 30.0) -> Page | None:
    """Rendered HTML and revision id of an article; ``None`` when unreadable."""
    params = {
        "action": "parse", "format": "json", "formatversion": "2",
        "prop": "text|revid", "maxlag": "5",
    }
    if ref.oldid:
        params["oldid"] = str(ref.oldid)
    else:
        params.update(page=ref.title, redirects="1")
    request = Request(f"{API}?{urlencode(params)}", headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        parsed = payload["parse"]
        return Page(ref, int(parsed["revid"]), tuple(tables_from_html(parsed["text"])))
    except (OSError, ValueError, KeyError, TypeError):
        return None


class PageStore:
    """Fetches each cited article once per run, politely spaced."""

    def __init__(
        self, fetch: Callable[[PageRef], Page | None] = fetch_page, *,
        min_interval: float = 1.5, sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._fetch, self._interval, self._sleep = fetch, min_interval, sleep
        self._pages: dict[str, Page | None] = {}
        self.requests = 0

    def get(self, ref: PageRef) -> Page | None:
        if ref.key not in self._pages:
            if self.requests:
                self._sleep(self._interval)
            self.requests += 1
            self._pages[ref.key] = self._fetch(ref)
        return self._pages[ref.key]


def check(
    rec: dict[str, Any], category: str, store: PageStore
) -> tuple[Decision, Page | None]:
    """Check a record against every English Wikipedia article it cites."""
    refs = [r for r in (page_ref(u) for u in rec.get("source_urls") or []) if r]
    if not refs:
        return Decision(NOPAGE), None
    best: tuple[Decision, Page | None] | None = None
    rank = {CONFIRM: 4, CONTRADICT: 3, INSUFFICIENT: 2, NOTFOUND: 1}
    for ref in dict.fromkeys(refs):
        page = store.get(ref)
        if page is None:
            continue
        decision = check_record(rec, category, page)
        if best is None or rank[decision.decision] > rank[best[0].decision]:
            best = (decision, page)
    # An unreadable article is not a verdict: nothing is cached for it.
    return best if best is not None else (Decision(NOPAGE), None)
