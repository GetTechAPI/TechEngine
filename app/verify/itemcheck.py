"""Tier 2 — compare a record with the Wikidata item it cites.

Software, website and many device records cite the exact Wikidata item they
were taken from. Searching Wikidata by name (``crossref``) ignores that citation
and has to guess; this check reads the cited item and compares what both state.

* the record's name must be the item's English label or one of its aliases
* each field the record carries is compared with the item's claims: dates at the
  coarser of the two precisions, URLs without scheme and ``www.``, names of
  developers, licences and the like against the labels of the claimed items
* :data:`MIN_FIELDS` agreeing fields and no conflict confirm a record
* a field the item states differently is a ``contradict``
* a deleted or redirected item is ``noitem``

A confirm means "the record states what the cited item states". The item's
revision id is stored with the decision.

Network access goes through an injected fetcher; the comparison is pure.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .common import STATE_DIR
from .rowcheck import normalize_name
from .wikidata import USER_AGENT, qid_of

CACHE_PATH = STATE_DIR / "itemcheck_cache.jsonl"
API = "https://www.wikidata.org/w/api.php"
BATCH = 50

CONFIRM = "confirm"
CONTRADICT = "contradict"
INSUFFICIENT = "insufficient"
NAME_MISMATCH = "name-mismatch"
NOITEM = "noitem"

AGREE, CONFLICT, NA = "agree", "conflict", "na"

DATE, LABELS, URL, YEAR = "date", "labels", "url", "year"


@dataclass(frozen=True)
class FieldSpec:
    name: str  # record field
    kind: str
    props: tuple[str, ...]  # Wikidata properties that state it


def _f(name: str, kind: str, *props: str) -> FieldSpec:
    return FieldSpec(name, kind, props)


_DEVICE = (
    _f("release_date", DATE, "P577", "P571", "P729", "P1619"),
    _f("brand", LABELS, "P176", "P1716", "P178"),
    _f("os", LABELS, "P306"),
)

FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    "software": (
        _f("release_date", DATE, "P577", "P571"),
        _f("developers", LABELS, "P178"),
        _f("publishers", LABELS, "P123"),
        _f("licenses", LABELS, "P275"),
        _f("programming_languages", LABELS, "P277"),
        _f("operating_systems", LABELS, "P306"),
        _f("genres", LABELS, "P136"),
    ),
    "website": (
        _f("homepage_url", URL, "P856"),
        _f("launch_date", DATE, "P571", "P1619", "P577"),
        _f("languages", LABELS, "P407", "P2936", "P364"),
        _f("owners", LABELS, "P127"),
    ),
    "brand": (
        _f("founded_year", YEAR, "P571"),
        _f("website", URL, "P856"),
    ),
    "smartphone": _DEVICE, "tablet": _DEVICE, "watch": _DEVICE,
    "pda": _DEVICE, "laptop": _DEVICE, "monitor": _DEVICE,
}

# Agreeing fields needed beside the name. A device's maker is all but part of
# its name, so a device needs a second field.
MIN_FIELDS: dict[str, int] = {"software": 1, "website": 1, "brand": 1}
MIN_FIELDS_DEFAULT = 2


# --- reading an item -------------------------------------------------------------


def _claims(entity: dict[str, Any], props: Iterable[str]) -> Iterator[Any]:
    """Values of non-deprecated claims for ``props``."""
    for prop in props:
        for claim in entity.get("claims", {}).get(prop, []):
            if claim.get("rank") == "deprecated":
                continue
            try:
                yield claim["mainsnak"]["datavalue"]["value"]
            except (KeyError, TypeError):
                continue


def target_ids(entity: dict[str, Any], category: str) -> set[str]:
    """Items whose labels are needed to compare this entity's label fields."""
    out: set[str] = set()
    for spec in FIELDS.get(category, ()):
        if spec.kind == LABELS:
            for value in _claims(entity, spec.props):
                if isinstance(value, dict) and isinstance(value.get("id"), str):
                    out.add(value["id"])
    return out


def names_of(entity: dict[str, Any]) -> set[str]:
    """Normalized labels and aliases an item goes by."""
    out: set[str] = set()
    for label in entity.get("labels", {}).values():
        if isinstance(label, dict):
            out.add(normalize_name(str(label.get("value", ""))))
    for group in entity.get("aliases", {}).values():
        for alias in group if isinstance(group, list) else []:
            if isinstance(alias, dict):
                out.add(normalize_name(str(alias.get("value", ""))))
    out.discard("")
    return out


# --- field comparison ------------------------------------------------------------

_TIME_RE = re.compile(r"^[+-]?(\d{4})-(\d{2})-(\d{2})T")
_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


def _record_date(value: Any) -> tuple[tuple[int, int, int], int] | None:
    """``((y, m, d), precision)`` with 9 = year, 10 = month, 11 = day.

    The first of a month is a month (in January, a year) written out."""
    if isinstance(value, int) and not isinstance(value, bool):
        return (value, 1, 1), 9
    match = _DATE_RE.match(value) if isinstance(value, str) else None
    if not match:
        return None
    year, month, day = int(match[1]), int(match[2] or 1), int(match[3] or 1)
    precision = 11 if day != 1 else 10 if month != 1 else 9
    return (year, month, day), precision


def cmp_date(value: Any, claims: list[Any]) -> str:
    ours = _record_date(value)
    if ours is None:
        return NA
    seen = False
    for claim in claims:
        match = _TIME_RE.match(str(claim.get("time", ""))) if isinstance(claim, dict) else None
        precision = claim.get("precision") if match else None
        if not match or not isinstance(precision, int) or precision < 9:
            continue  # a decade or century says nothing about a year
        seen = True
        theirs = (int(match[1]), int(match[2]) or 1, int(match[3]) or 1)
        depth = min(ours[1], precision, 11) - 8  # 1 = year, 2 = month, 3 = day
        if ours[0][:depth] == theirs[:depth]:
            return AGREE
    return CONFLICT if seen else NA


def url_key(url: Any) -> str:
    """Host and path of a URL, without scheme, ``www.`` and a trailing slash."""
    if not isinstance(url, str):
        return ""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return ""
    host = parsed.netloc.lower().split("@")[-1]
    host = host[4:] if host.startswith("www.") else host
    if not host:
        return ""
    query = f"?{parsed.query}" if parsed.query else ""
    return host + parsed.path.rstrip("/") + query


def cmp_url(value: Any, claims: list[Any]) -> str:
    ours = url_key(value)
    theirs = {url_key(c) for c in claims} - {""}
    if not ours or not theirs:
        return NA
    return AGREE if ours in theirs else CONFLICT


def cmp_labels(value: Any, claims: list[Any], labels: dict[str, set[str]]) -> str:
    """Every name the record lists must be a name of one of the claimed items."""
    ours = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    wanted = {normalize_name(v) for v in ours if isinstance(v, str)} - {""}
    if not wanted:
        return NA
    known: set[str] = set()
    resolved = False
    for claim in claims:
        names = labels.get(claim.get("id", "")) if isinstance(claim, dict) else None
        if names:
            resolved = True
            known |= names
    if not resolved:
        return NA
    return AGREE if wanted <= known else CONFLICT


@dataclass(frozen=True)
class Decision:
    decision: str
    agreements: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


def check_record(
    rec: dict[str, Any], category: str, entity: dict[str, Any], labels: dict[str, set[str]]
) -> Decision:
    """Compare ``rec`` with the entity it cites. ``labels`` maps the ids of
    claimed items to their normalized names."""
    if "missing" in entity or "redirects" in entity or not entity.get("claims"):
        return Decision(NOITEM)
    wanted = {normalize_name(n) for n in [rec.get("name"), *(rec.get("aliases") or [])]
              if isinstance(n, str)} - {""}
    if not wanted & names_of(entity):
        return Decision(NAME_MISMATCH)
    agreements: list[str] = []
    conflicts: list[str] = []
    for spec in FIELDS.get(category, ()):
        value = rec.get(spec.name)
        claims = list(_claims(entity, spec.props))
        if value in (None, "", []) or not claims:
            continue
        if spec.kind in (DATE, YEAR):
            verdict = cmp_date(value, claims)
        elif spec.kind == URL:
            verdict = cmp_url(value, claims)
        else:
            verdict = cmp_labels(value, claims, labels)
        if verdict == AGREE:
            agreements.append(spec.name)
        elif verdict == CONFLICT:
            conflicts.append(spec.name)
    if conflicts:
        return Decision(CONTRADICT, tuple(agreements), tuple(conflicts))
    if len(agreements) >= MIN_FIELDS.get(category, MIN_FIELDS_DEFAULT):
        return Decision(CONFIRM, tuple(agreements))
    return Decision(INSUFFICIENT, tuple(agreements))


# --- fetching --------------------------------------------------------------------

Fetch = Callable[[list[str], str], dict[str, Any] | None]


def fetch_entities(ids: list[str], props: str, *, timeout: float = 60.0) -> dict[str, Any] | None:
    """One ``wbgetentities`` call; ``None`` when Wikidata gave no usable answer.

    Redirects are not followed: a redirected citation is a dead one."""
    params = urlencode({
        "action": "wbgetentities", "ids": "|".join(ids), "props": props,
        "languages": "en|mul", "redirects": "no", "format": "json", "maxlag": "5",
    })
    request = Request(f"{API}?{params}", headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return None
    entities = payload.get("entities") if isinstance(payload, dict) else None
    return entities if isinstance(entities, dict) and "error" not in payload else None


class ItemStore:
    """Batched, spaced access to items and to the names of the items they claim."""

    def __init__(
        self, fetch: Fetch = fetch_entities, *, min_interval: float = 1.0,
        retries: int = 3, sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._fetch, self._interval, self._retries, self._sleep = (
            fetch, min_interval, retries, sleep)
        self.labels: dict[str, set[str]] = {}
        self.requests = 0

    def _call(self, ids: list[str], props: str) -> dict[str, Any] | None:
        for attempt in range(self._retries):
            if self.requests:
                self._sleep(self._interval * (1 + 4 * attempt))
            self.requests += 1
            got = self._fetch(ids, props)
            if got is not None:
                return got
        return None

    def entities(self, ids: list[str]) -> dict[str, Any]:
        """Entities with claims, labels and aliases; unreadable ones are absent."""
        out: dict[str, Any] = {}
        for start in range(0, len(ids), BATCH):
            got = self._call(ids[start:start + BATCH], "info|labels|aliases|claims")
            out.update(got or {})
        return out

    def resolve(self, ids: Iterable[str]) -> None:
        """Load the names of claimed items not seen yet."""
        todo = sorted(set(ids) - set(self.labels))
        for start in range(0, len(todo), BATCH):
            batch = todo[start:start + BATCH]
            got = self._call(batch, "labels|aliases") or {}
            for qid in batch:
                if isinstance(got.get(qid), dict):
                    self.labels[qid] = names_of(got[qid])


def cited_items(rec: dict[str, Any]) -> list[str]:
    urls = rec.get("source_urls") or []
    return list(dict.fromkeys(q for q in (qid_of(u) for u in urls) if q))
