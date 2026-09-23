"""Backfill PhoneDB URLs onto Kaggle-only phone, tablet, and watch records."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.verify import ledger
from app.verify.crossref import (
    AMBIGUOUS,
    CONFIRM,
    CONTRADICT,
    NOTFOUND,
    Candidate,
    _heading_matches,
    normalize_heading,
)
from app.verify.http_check import USER_AGENT, classify

_INCH_RE = re.compile(r"(\d+(?:\.\d+)?)(?:\s*(?:inches|inch)|\s*[\"″])", re.IGNORECASE)
_RES_RE = re.compile(r"(\d{2,5})\s*[x×]\s*(\d{2,5})", re.IGNORECASE)
_MAH_RE = re.compile(r"(\d{3,5})\s*mAh", re.IGNORECASE)
_SOURCE_URLS_RE = re.compile(
    r'("source_urls"\s*:\s*\[)(?P<body>.*?)(\n)(?P<indent>[ \t]*)\]',
    re.DOTALL,
)
DECISIONS = (CONFIRM, AMBIGUOUS, NOTFOUND, CONTRADICT)
MIN_SLEEP_S = 1.0
STOP_RETRY_AFTER_S = 5 * 60
# Do not turn a batch run into an unattended multi-hour wait. A server can
# ask for up to 15 minutes; anything over five minutes ends this host run
# cleanly and leaves the remaining records eligible for a later invocation.
# Rate-limits and transport failures are not evidence the phone is missing.
RETRY_LIVENESS = frozenset({"error", "http-403", "http-408", "http-429", "http-503"})
FetchFn = Callable[[str], tuple[int | None, str, str]]
HttpGetFn = Callable[[str], tuple[int | None, str, str, str | None]]


class HttpClient(Protocol):
    requests: int
    retry_after_s: float | None

    def fetch(self, url: str) -> tuple[int | None, str, str]: ...


def disambiguate(name: str, candidates: list[Candidate]) -> list[Candidate]:
    """Collapse heading hits to one phone, or return every remaining hit.

    A single normalized-equality hit wins over extra suffix hits (those are
    longer different models that the shared suffix rule also accepts). More
    than one remaining hit is ambiguous and must not be guessed.
    """
    by_url: dict[str, Candidate] = {}
    for cand in candidates:
        by_url.setdefault(cand.url, cand)
    unique = list(by_url.values())
    norm = normalize_heading(name)
    exact = [c for c in unique if norm and normalize_heading(c.title) == norm]
    if exact:
        return exact
    return unique


# --- page specs + gate -----------------------------------------------------------


@dataclass
class PageSpecs:
    title: str
    battery_mah: int | None = None
    ram_gb: list[float] = field(default_factory=list)
    size_inch: float | None = None
    resolution: tuple[int, int] | None = None
    weight_g: float | None = None


def _first_int(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    if match is None:
        return None
    return int(match.group(1))


def _parse_inches(text: str) -> float | None:
    match = _INCH_RE.search(text)
    if match is None:
        return None
    return float(match.group(1))


def _parse_resolution(text: str) -> tuple[int, int] | None:
    match = _RES_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number <= 0:
        return None
    return number


def _resolutions_equal(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left == right or left == (right[1], right[0])


def compare_specs(record: dict[str, Any], page: PageSpecs) -> tuple[list[str], list[str]]:
    """Return ``(agreements, conflicts)`` for fields present on both sides."""
    agreements: list[str] = []
    conflicts: list[str] = []
    battery = _positive_number(record.get("battery_mah"))
    if battery is not None and page.battery_mah is not None:
        if int(battery) == page.battery_mah:
            agreements.append("battery_mah")
        else:
            conflicts.append("battery_mah")
    ram = _positive_number(record.get("ram_gb"))
    if ram is not None and page.ram_gb:
        if any(abs(ram - found) <= 0.05 for found in page.ram_gb):
            agreements.append("ram_gb")
        else:
            conflicts.append("ram_gb")
    display = record.get("display")
    display = display if isinstance(display, dict) else {}
    size = _positive_number(display.get("size_inch"))
    if size is not None and page.size_inch is not None:
        if abs(size - page.size_inch) <= 0.05:
            agreements.append("display_size_inch")
        else:
            conflicts.append("display_size_inch")
    record_res = _parse_resolution(str(display.get("resolution") or ""))
    if record_res is not None and page.resolution is not None:
        if _resolutions_equal(record_res, page.resolution):
            agreements.append("display_resolution")
        else:
            conflicts.append("display_resolution")
    weight = _positive_number(record.get("weight_g"))
    if weight is not None and page.weight_g is not None:
        if abs(weight - page.weight_g) <= 2.0:
            agreements.append("weight_g")
        else:
            conflicts.append("weight_g")
    return agreements, conflicts


@dataclass
class GateResult:
    decision: str
    proposed_url: str | None
    inspected_url: str | None
    title: str | None
    liveness: str | None
    agreements: list[str]
    conflicts: list[str]
    reason: str
    suffix_only: bool


def gate_page(
    record: dict[str, Any],
    candidate: Candidate,
    status: int | None,
    final_url: str | None,
    html_text: str,
    heading_matches: Callable[[str, str], bool] | None = None,
) -> GateResult:
    """Two-step gate: liveness via ``classify``, then title + at least one spec."""
    alive, liveness = classify(candidate.url, status, final_url)
    if not alive:
        return GateResult(NOTFOUND, None, None, None, liveness, [], [], "not-live", False)
    page = parse_page(html_text)
    name = record.get("name") if isinstance(record.get("name"), str) else ""
    title = page.title
    matches = heading_matches or _heading_matches
    if not name or not title or not matches(name, title):
        return GateResult(
            AMBIGUOUS, None, candidate.url, title or None, liveness, [], [], "title-mismatch", False
        )
    agreements, conflicts = compare_specs(record, page)
    suffix_only = normalize_heading(name) != normalize_heading(title)
    if agreements:
        return GateResult(
            CONFIRM,
            candidate.url,
            candidate.url,
            title,
            liveness,
            agreements,
            conflicts,
            "spec-agree",
            suffix_only,
        )
    if conflicts:
        return GateResult(
            CONTRADICT,
            None,
            candidate.url,
            title,
            liveness,
            [],
            conflicts,
            "spec-conflict",
            suffix_only,
        )
    return GateResult(
        AMBIGUOUS,
        None,
        candidate.url,
        title,
        liveness,
        [],
        [],
        "no-comparable-spec",
        suffix_only,
    )


# --- cache (ledger JSONL pattern, without ledger.append's data-dir mkdir) --------


def content_hash(record: dict[str, Any]) -> str:
    blob = json.dumps(record, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def append_cache(entry: dict[str, Any], path: Path) -> None:
    """Append one JSONL decision. Line format matches ``ledger.append``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    """Latest cache row per repo-relative path. Later lines override earlier."""
    out: dict[str, dict[str, Any]] = {}
    for entry in ledger.iter_entries(path):
        key = entry.get("path")
        if isinstance(key, str):
            out[key] = entry
    return out


def cache_entry(
    *,
    rel_path: str,
    record: dict[str, Any],
    result: GateResult,
    ts: str,
) -> dict[str, Any]:
    return {
        "agreements": result.agreements,
        "conflicts": result.conflicts,
        "decision": result.decision,
        "hash": content_hash(record),
        "inspected_url": result.inspected_url,
        "liveness": result.liveness,
        "name": record.get("name"),
        "path": rel_path,
        "proposed_url": result.proposed_url,
        "reason": result.reason,
        "slug": record.get("slug"),
        "suffix_only": result.suffix_only,
        "title": result.title,
        "ts": ts,
    }


# --- TechAPI reads (working tree, else git show — never a write) -----------------


def git_toplevel(path: Path) -> Path:
    proc = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(proc.stdout.strip())


def parse_cat_file_batch(data: bytes, paths: list[str]) -> dict[str, dict[str, Any]]:
    """Parse ``git cat-file --batch`` output aligned with the requested paths."""
    out: dict[str, dict[str, Any]] = {}
    cursor = 0
    for rel in paths:
        newline = data.find(b"\n", cursor)
        if newline < 0:
            break
        header = data[cursor:newline].decode("utf-8", "replace")
        cursor = newline + 1
        parts = header.split()
        if len(parts) < 2 or parts[-1] == "missing":
            continue
        size = int(parts[-1])
        blob = data[cursor : cursor + size]
        cursor += size + 1
        out[rel] = json.loads(blob.decode("utf-8-sig"))
    return out


def load_records(repo: Path, paths: list[str]) -> dict[str, dict[str, Any]]:
    if not paths:
        return {}
    payload = "".join(f"HEAD:{rel}\n" for rel in paths).encode()
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        input=payload,
        capture_output=True,
        check=True,
    )
    return parse_cat_file_batch(proc.stdout, paths)


def sample_diverse(paths: list[str], limit: int | None) -> list[str]:
    """Round-robin across brands so a limit is not the first brand alphabetically."""
    by_brand: dict[str, list[str]] = {}
    for rel in sorted(paths):
        by_brand.setdefault(brand_of(rel), []).append(rel)
    if limit is None:
        return [rel for brand in sorted(by_brand) for rel in by_brand[brand]]
    picked: list[str] = []
    cursors = {brand: 0 for brand in by_brand}
    while len(picked) < limit:
        progressed = False
        for brand in sorted(by_brand):
            index = cursors[brand]
            bucket = by_brand[brand]
            if index >= len(bucket):
                continue
            picked.append(bucket[index])
            cursors[brand] = index + 1
            progressed = True
            if len(picked) >= limit:
                break
        if not progressed:
            break
    return picked


# --- source_urls edit (not used by --dry-run) ------------------------------------


def add_source_url_text(raw: str, url: str) -> str | None:
    """Insert ``url`` into the ``source_urls`` array without reformatting the file.

    Returns None when the URL is already present or the array cannot be found,
    so a caller leaves the file untouched.
    """
    if f'"{url}"' in raw:
        return None
    match = _SOURCE_URLS_RE.search(raw)
    if match is None:
        return None
    body = match.group("body")
    indent_match = re.search(r'\n([ \t]+)"https?://', body)
    entry_indent = indent_match.group(1) if indent_match else match.group("indent") + "  "
    if re.search(r'"https?://', body):
        body, replaced = re.subn(r'("https?://[^"\n]*")(\s*)$', r"\1,\2", body, count=1)
        if replaced != 1:
            return None
    insertion = f'\n{entry_indent}"{url}"'
    return raw[: match.start("body")] + body + insertion + raw[match.end("body") :]


def write_source_url_if_unchanged(path: Path, record: dict[str, Any], url: str) -> bool:
    """Add ``url`` only if the checked-out file still matches the reviewed record.

    Records are read from ``HEAD`` to support sparse checkouts. Before a real
    write, re-read the working-tree file and compare its parsed JSON so a
    concurrent edit (or an absent sparse file) can never receive a stale gate.
    """
    if not path.is_file():
        return False
    try:
        raw = path.read_bytes().decode("utf-8")
        current = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(current, dict) or content_hash(current) != content_hash(record):
        return False
    updated = add_source_url_text(raw, url)
    if updated is None:
        source_urls = current.get("source_urls")
        return isinstance(source_urls, list) and url in source_urls
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(updated.encode("utf-8"))
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


# --- HTTP ------------------------------------------------------------------------


def http_get_with_headers(
    url: str, timeout: float = 60.0
) -> tuple[int | None, str, str, str | None]:
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", None) or response.getcode())
            final = response.geturl()
            body = response.read().decode("utf-8", "replace")
            return status, final, body, response.headers.get("Retry-After")
    except HTTPError as exc:
        raw = exc.read() if exc.fp is not None else b""
        final = exc.geturl() if hasattr(exc, "geturl") else url
        return int(exc.code), final, raw.decode("utf-8", "replace"), exc.headers.get("Retry-After")
    except URLError:
        return None, url, "", None


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Parse an HTTP Retry-After delta or date, rejecting invalid/past values."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = (retry_at - (now or datetime.now(UTC))).total_seconds()
    return max(0.0, seconds) if seconds >= 0 else None


@dataclass
class PoliteClient:
    """At least ``MIN_SLEEP_S`` between outbound requests, regardless of ``--sleep 0``."""

    sleep_s: float
    get: HttpGetFn = http_get_with_headers
    requests: int = 0
    _last: float = 0.0
    retry_after_s: float | None = None

    def fetch(self, url: str) -> tuple[int | None, str, str]:
        gap = max(self.sleep_s, MIN_SLEEP_S)
        if self.requests:
            wait = gap - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
        self.requests += 1
        self._last = time.monotonic()
        status, final, body, retry_after = self.get(url)
        self.retry_after_s = parse_retry_after(retry_after)
        return status, final, body

    def fetch_post(self, url: str, form: dict[str, str]) -> tuple[int | None, str, str]:
        gap = max(self.sleep_s, MIN_SLEEP_S)
        if self.requests:
            wait = gap - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
        self.requests += 1
        self._last = time.monotonic()
        data = urlencode(form).encode()
        request = Request(
            url,
            data=data,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urlopen(request, timeout=60) as response:
                self.retry_after_s = parse_retry_after(response.headers.get("Retry-After"))
                return (
                    int(getattr(response, "status", None) or response.getcode()),
                    response.geturl(),
                    response.read().decode("utf-8", "replace"),
                )
        except HTTPError as exc:
            self.retry_after_s = parse_retry_after(exc.headers.get("Retry-After"))
            return int(exc.code), exc.geturl(), exc.read().decode("utf-8", "replace")
        except URLError:
            self.retry_after_s = None
            return None, url, ""


def _outside_repo(path: Path, repo: Path) -> None:
    resolved = path.resolve()
    root = repo.resolve()
    if resolved == root or root in resolved.parents:
        raise SystemExit(f"refusing to write inside the TechAPI repo: {resolved}")


@dataclass
class RunResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    brands: set[str] = field(default_factory=set)
    cached: int = 0
    requests: int = 0
    index_size: int = 0
    skipped_category: int = 0
    stopped: str | None = None

    def counts(self) -> dict[str, int]:
        totals = {name: 0 for name in DECISIONS}
        for row in self.rows:
            decision = row.get("decision")
            if isinstance(decision, str) and decision in totals:
                totals[decision] += 1
        return totals


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _row_from_cache(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "agreements": entry.get("agreements") or [],
        "conflicts": entry.get("conflicts") or [],
        "decision": entry.get("decision"),
        "inspected_url": entry.get("inspected_url"),
        "name": entry.get("name"),
        "path": entry.get("path"),
        "proposed_url": entry.get("proposed_url"),
        "reason": entry.get("reason"),
        "suffix_only": bool(entry.get("suffix_only")),
        "title": entry.get("title"),
        "cached": True,
    }


def _row_from_result(rel: str, record: dict[str, Any], result: GateResult) -> dict[str, Any]:
    return {
        "agreements": result.agreements,
        "conflicts": result.conflicts,
        "decision": result.decision,
        "inspected_url": result.inspected_url,
        "name": record.get("name"),
        "path": rel,
        "proposed_url": result.proposed_url,
        "reason": result.reason,
        "suffix_only": result.suffix_only,
        "title": result.title,
        "cached": False,
    }


def _visible_heading(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def punctuation_only_match(name: str, title: str) -> bool:
    """Normalized equality that is not a visible equality (``S20+`` vs ``S20``)."""
    if not name or not title:
        return False
    if normalize_heading(name) != normalize_heading(title):
        return False
    return _visible_heading(name) != _visible_heading(title)


def render_summary(result: RunResult, *, dry_run: bool, sleep_s: float) -> str:
    counts = result.counts()
    total = len(result.rows) or 1
    lines = [
        "# GSMArena backfill dry-run" if dry_run else "# GSMArena backfill",
        "",
        f"- records: **{len(result.rows)}** across **{len(result.brands)}** brands",
        f"- index phones: {result.index_size}",
        f"- http requests this process: {result.requests}",
        f"- sleep between requests: {max(sleep_s, MIN_SLEEP_S):.1f}s",
        f"- resumed from cache: {result.cached}",
        f"- dry-run: {dry_run}",
        "",
        "## Decisions",
        "",
        "| decision | count | ratio |",
        "| --- | ---: | ---: |",
    ]
    for name in DECISIONS:
        count = counts[name]
        lines.append(f"| {name.upper()} | {count} | {count / total:.1%} |")
    confirms = [row for row in result.rows if row.get("decision") == CONFIRM]
    conflicts = [row for row in result.rows if row.get("conflicts")]
    lines.extend(
        [
            "",
            f"- CONFIRM rate: {len(confirms) / total:.1%}",
            f"- records with spec conflicts: {len(conflicts)} ({len(conflicts) / total:.1%})",
        ]
    )
    lines.append("")
    suffix_confirms = [
        row
        for row in result.rows
        if row["decision"] == CONFIRM and row.get("suffix_only") and row.get("proposed_url")
    ]
    ram_only = [
        row
        for row in result.rows
        if row["decision"] == CONFIRM and row.get("agreements") == ["ram_gb"]
    ]
    conflicted_confirms = [
        row for row in result.rows if row["decision"] == CONFIRM and row.get("conflicts")
    ]
    folded = [
        row
        for row in result.rows
        if row["decision"] == CONFIRM
        and punctuation_only_match(str(row.get("name") or ""), str(row.get("title") or ""))
    ]
    lines.extend(
        [
            "## Mismatch signals",
            "",
            f"- CONFIRM via suffix title only (not normalized equality): {len(suffix_confirms)}",
            f"- CONFIRM whose only agreeing spec is RAM: {len(ram_only)}",
            f"- CONFIRM that also has a disagreeing spec: {len(conflicted_confirms)}",
            f"- CONFIRM where punctuation was folded away (S20+ vs S20): {len(folded)}",
            "",
        ]
    )
    for label, bucket in (
        ("Suffix-only CONFIRM", suffix_confirms),
        ("RAM-only CONFIRM", ram_only),
        ("CONFIRM with a spec conflict", conflicted_confirms),
        ("Punctuation-folded CONFIRM", folded),
    ):
        if not bucket:
            continue
        lines.append(f"### {label}")
        lines.append("")
        for row in bucket[:30]:
            lines.append(
                f"- `{row['name']}` → {row.get('proposed_url')} "
                f"(title `{row.get('title')}`, agree {row.get('agreements')}, "
                f"conflict {row.get('conflicts')})"
            )
        lines.append("")
    lines.append("## Proposed source_urls (CONFIRM only)")
    lines.append("")
    confirms = [
        row for row in result.rows if row["decision"] == CONFIRM and row.get("proposed_url")
    ]
    if not confirms:
        lines.append("None.")
    for row in confirms:
        lines.append(
            f"- `{row['path']}` — {row.get('name')!r} vs {row.get('title')!r} "
            f"→ {row.get('proposed_url')} (agree {', '.join(row.get('agreements') or [])})"
        )
    lines.append("")
    lines.append("## Unresolved")
    lines.append("")
    unresolved = [row for row in result.rows if row["decision"] != CONFIRM]
    if not unresolved:
        lines.append("None.")
    for row in unresolved:
        lines.append(
            f"- `{row.get('decision', '').upper()}` `{row['name']}` "
            f"({row.get('reason')}; inspected {row.get('inspected_url') or '—'})"
        )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# implementation below intentionally reuses the surrounding cache, liveness,
# guarded source_urls writer and two-stage gate from this module.
PHONEDB_URL = "https://phonedb.net/index.php?m=device&s=list"
DATA_CATEGORIES = ("smartphone", "tablet", "watch")
# PhoneDB's list endpoint emits one ``content_block_title`` anchor per result.
# Its current markup happens to put ``title`` before ``href``; accept either
# order and insignificant whitespace so the parser follows the page structure.
_PHONEDB_RESULT_RE = re.compile(
    r'<div\b[^>]*\bclass\s*=\s*["\']content_block_title["\'][^>]*>\s*'
    r'<a\b(?=[^>]*\btitle\s*=\s*["\'](?P<title>[^"\']+)["\'])'
    r'(?=[^>]*\bhref\s*=\s*["\'](?P<href>[^"\']+)["\'])[^>]*>',
    re.I,
)
_PHONEDB_H1_RE = re.compile(r"<h1>(.*?)</h1>", re.I | re.S)


def is_kaggle_record(record: dict[str, Any]) -> bool:
    """Select records whose only citation is a Kaggle dataset URL."""
    sources = record.get("source_urls")
    return (
        isinstance(sources, list)
        and len(sources) == 1
        and isinstance(sources[0], str)
        and sources[0].lower().startswith(
            (
                "https://www.kaggle.com/",
                "https://kaggle.com/",
                "http://www.kaggle.com/",
                "http://kaggle.com/",
            )
        )
    )


def list_kaggle_paths(repo: Path) -> list[str]:
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "ls-tree",
            "-r",
            "--name-only",
            "HEAD",
            *[f"data/{category}" for category in DATA_CATEGORIES],
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in proc.stdout.splitlines() if line.endswith(".json")]


def brand_of(rel_path: str) -> str:
    parts = rel_path.split("/")
    return parts[2] if len(parts) >= 3 and parts[0] == "data" and parts[1] in DATA_CATEGORIES else ""


def _phonedb_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _phonedb_heading_matches(record_name: str, result_title: str) -> bool:
    """Require PhoneDB's result title to exactly match the record name."""
    return normalize_heading(record_name) == normalize_heading(result_title)


def parse_page(html_text: str) -> PageSpecs:
    """Read PhoneDB's visible heading and specs from its table/meta content."""
    text = _phonedb_text(html_text)
    heading = _PHONEDB_H1_RE.search(html_text)
    title = _phonedb_text(heading.group(1)) if heading else ""
    ram = [float(m.group(1)) for m in re.finditer(r"(\d+(?:\.\d+)?)\s*GiB\s*RAM", text, re.I)]
    battery = _first_int(_MAH_RE, text)
    size = _parse_inches(text)
    resolution = _parse_resolution(text)
    return PageSpecs(
        title=title, battery_mah=battery, ram_gb=ram, size_inch=size, resolution=resolution
    )


@dataclass
class PhoneDbFetcher:
    client: PoliteClient
    stopped: str | None = None

    def search(self, name: str) -> list[Candidate]:
        # POST is PhoneDB's documented quick-search form; no third-party search
        # engine is used, and each request is rate-limited by PoliteClient.
        status, _final, body = self.client.fetch_post(
            PHONEDB_URL, {"search_exp": name, "search_header": "Search"}
        )
        if status == 429:
            retry_after = self.client.retry_after_s
            if retry_after is None:
                self.stopped = "PhoneDB returned 429 without a usable Retry-After; stopped"
                return []
            if retry_after > STOP_RETRY_AFTER_S:
                self.stopped = (
                    f"PhoneDB requested Retry-After={retry_after:.0f}s; "
                    "stopped before sending more requests"
                )
                return []
            time.sleep(retry_after)
            status, _final, body = self.client.fetch_post(
                PHONEDB_URL, {"search_exp": name, "search_header": "Search"}
            )
            if status == 429:
                self.stopped = "PhoneDB returned repeated 429 responses; stopped"
                return []
        if status is None or status >= 400:
            return []
        found: list[Candidate] = []
        for match in _PHONEDB_RESULT_RE.finditer(body):
            raw_title, href = match.group("title"), match.group("href")
            title = re.sub(r"\s*\([^)]*\)\s*$", "", _phonedb_text(raw_title)).strip()
            url = html.unescape(href)
            if url.startswith("index.php"):
                url = "https://phonedb.net/" + url
            if _phonedb_heading_matches(name, title):
                found.append(Candidate(title=title, url=url))
        return disambiguate(name, found)


def evaluate_record(record: dict[str, Any], fetcher: PhoneDbFetcher, fetch: FetchFn) -> GateResult:
    name = record.get("name")
    if not isinstance(name, str) or not name.strip():
        return GateResult(NOTFOUND, None, None, None, None, [], [], "no-name", False)
    candidates = fetcher.search(name)
    if not candidates:
        return GateResult(NOTFOUND, None, None, None, None, [], [], "no-heading-match", False)
    if len(candidates) != 1:
        return GateResult(AMBIGUOUS, None, None, None, None, [], [], "ambiguous-heading", False)
    status, final_url, body = fetch(candidates[0].url)
    if status is None:
        return GateResult(NOTFOUND, None, None, None, "error", [], [], "network-error", False)
    return gate_page(record, candidates[0], status, final_url, body)


def backfill(
    *,
    repo: Path,
    cache_path: Path,
    index_path: Path,
    summary_path: Path,
    limit: int | None,
    sleep_s: float,
    dry_run: bool,
    refresh_index: bool,
    client: HttpClient | None = None,
    paths: list[str] | None = None,
    records: dict[str, dict[str, Any]] | None = None,
) -> RunResult:
    del index_path, refresh_index
    _outside_repo(cache_path, repo)
    _outside_repo(summary_path, repo)
    client = client or PoliteClient(sleep_s=sleep_s)
    if not isinstance(client, PoliteClient):
        raise TypeError("PhoneDB backfill requires a PoliteClient")
    cache = load_cache(cache_path)
    rel_paths = paths if paths is not None else list_kaggle_paths(repo)
    loaded = records if records is not None else load_records(repo, rel_paths)
    eligible = [p for p in rel_paths if p in loaded and is_kaggle_record(loaded[p])]
    chosen = sample_diverse(eligible, limit)
    result = RunResult(index_size=0, skipped_category=len(rel_paths) - len(eligible))
    fetcher = PhoneDbFetcher(client)

    def fetch(url: str) -> tuple[int | None, str, str]:
        status, final, body = client.fetch(url)
        if status != 429:
            return status, final, body
        retry_after = client.retry_after_s
        if retry_after is None:
            result.stopped = "PhoneDB returned 429 without a usable Retry-After; stopped"
            return status, final, body
        if retry_after > STOP_RETRY_AFTER_S:
            result.stopped = (
                f"PhoneDB requested Retry-After={retry_after:.0f}s; "
                "stopped before sending more requests"
            )
            return status, final, body
        time.sleep(retry_after)
        status, final, body = client.fetch(url)
        if status == 429:
            result.stopped = "PhoneDB returned repeated 429 responses; stopped"
        return status, final, body

    for rel in chosen:
        record = loaded[rel]
        result.brands.add(brand_of(rel))
        digest = content_hash(record)
        cached = cache.get(rel)
        if cached and cached.get("hash") == digest and cached.get("decision") in DECISIONS:
            result.rows.append(_row_from_cache(cached))
            result.cached += 1
            continue
        outcome = evaluate_record(record, fetcher, fetch)
        if fetcher.stopped:
            result.stopped = fetcher.stopped
        if result.stopped:
            result.rows.append(_row_from_result(rel, record, outcome))
            print(result.stopped, flush=True)
            break
        if not dry_run and outcome.decision == CONFIRM and outcome.proposed_url:
            if not write_source_url_if_unchanged(repo / rel, record, outcome.proposed_url):
                outcome = GateResult(
                    "write-failed",
                    None,
                    outcome.inspected_url,
                    outcome.title,
                    outcome.liveness,
                    outcome.agreements,
                    outcome.conflicts,
                    "source-urls-write-failed-or-record-changed",
                    outcome.suffix_only,
                )
        if outcome.liveness not in RETRY_LIVENESS and outcome.reason != "network-error":
            append_cache(
                cache_entry(rel_path=rel, record=record, result=outcome, ts=_now_iso()), cache_path
            )
        result.rows.append(_row_from_result(rel, record, outcome))
        outcome_detail = outcome.proposed_url or outcome.reason
        print(
            f"{outcome.decision.upper()} {record.get('name')} {outcome_detail}",
            flush=True,
        )
    result.requests = client.requests
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        render_summary(result, dry_run=dry_run, sleep_s=sleep_s).replace("GSMArena", "PhoneDB"),
        encoding="utf-8",
    )
    return result


def _default_state_dir() -> Path:
    return Path(".phonedb-backfill")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.verify.phonedb_backfill")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=1.5)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    state = _default_state_dir()
    repo = git_toplevel(args.data_root)
    result = backfill(
        repo=repo,
        cache_path=args.cache or state / "cache.jsonl",
        index_path=state / "unused-index.json",
        summary_path=args.summary or state / "summary.md",
        limit=args.limit,
        sleep_s=args.sleep,
        dry_run=args.dry_run,
        refresh_index=False,
    )
    counts = result.counts()
    print(
        " ".join(f"{name.upper()}={counts[name]}" for name in DECISIONS)
        + f" records={len(result.rows)} requests={result.requests} dry_run={args.dry_run}"
    )
    if result.stopped:
        print(result.stopped, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
