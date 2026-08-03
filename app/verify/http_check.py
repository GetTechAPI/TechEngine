"""Tier 1 — source_urls liveness.

Answers "do this record's cited sources actually resolve?" without trusting the
page contents (that is Tier 2). Pure-ish: all network I/O goes through an injected
*opener* so tests run offline with a fake.

Design constraints (project memory): stdlib only (urllib + concurrent.futures),
per-host rate limiting, a resumable TTL cache, and never re-check fresh URLs.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlparse
from urllib.request import Request, build_opener

from . import ledger
from .common import STATE_DIR
from .hosts import host_of

URL_CACHE_PATH = STATE_DIR / "url_cache.jsonl"
DEFAULT_TTL_DAYS = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 TechAPI-verify/0.1"
)


# "Come back later" is not "this link is dead". A host that rate-limits us says
# nothing about whether the page exists, so these answers must never be cached as
# a verdict — otherwise one impatient run marks a whole host dead for a TTL.
TRANSIENT_STATUSES = frozenset({429, 503})
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_S = (2.0, 6.0)
MAX_RETRY_AFTER_S = 15.0
# How much to slow a host down for the rest of the run once it has pushed back.
RATE_LIMIT_PENALTY = 4.0
# The penalty is multiplicative, so without a ceiling a host that refuses often
# walks the interval up without bound (1s -> 4 -> 16 -> 64 -> ...) and a run over
# a few thousand URLs on that host turns into hours of sleeping.
MAX_HOST_INTERVAL_S = 30.0


class CheckResult(NamedTuple):
    url: str
    status: int | None
    final_url: str | None
    alive: bool
    reason: str

    @property
    def transient(self) -> bool:
        return self.status in TRANSIENT_STATUSES


# --- opener abstraction (injectable for tests) -----------------------------------


class _Opener:
    """Thin wrapper over urllib's opener exposing ``open(url, method) -> (status, final)``."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._opener = build_opener()
        self.timeout = timeout

    def open(self, url: str, method: str) -> tuple[int, str]:
        req = Request(url, method=method, headers={"User-Agent": USER_AGENT})
        resp = self._opener.open(req, timeout=self.timeout)
        try:
            status = getattr(resp, "status", None) or resp.getcode()
            final = resp.geturl()
            return int(status), final
        finally:
            resp.close()


def default_opener_factory(timeout: float = 10.0) -> _Opener:
    return _Opener(timeout=timeout)


# --- classification --------------------------------------------------------------


def _path_depth(url: str) -> int:
    try:
        path = urlparse(url).path.strip("/")
    except Exception:
        return 0
    return len([p for p in path.split("/") if p])


def _is_homepage_redirect(original: str, final: str) -> bool:
    """A deep page that redirects to the site root is a soft-404 ("not found" page)."""
    if not final or final == original:
        return False
    return _path_depth(original) >= 1 and _path_depth(final) == 0


def classify(original_url: str, status: int | None, final_url: str | None) -> tuple[bool, str]:
    if status is None:
        return False, "error"
    if status >= 400:
        return False, f"http-{status}"
    if final_url and _is_homepage_redirect(original_url, final_url):
        return False, "homepage-redirect"
    return True, f"http-{status}"


def _retry_after_seconds(exc: Exception) -> float | None:
    """Seconds requested by a ``Retry-After`` header, clamped to something sane."""
    headers = getattr(exc, "headers", None)
    raw = headers.get("Retry-After") if headers is not None else None
    try:
        return min(float(raw), MAX_RETRY_AFTER_S) if raw is not None else None
    except (TypeError, ValueError):
        return None  # HTTP-date form; fall back to our own backoff


def _attempt(url: str, opener: Any) -> tuple[int | None, str | None, float | None]:
    """One HEAD-then-GET pass. Returns (status, final_url, retry_after)."""
    status: int | None = None
    final: str | None = None
    retry_after: float | None = None
    for method in ("HEAD", "GET"):
        try:
            status, final = opener.open(url, method)
            if method == "HEAD" and status in (400, 403, 405, 501):
                continue  # server dislikes HEAD -> retry GET
            break
        except Exception as exc:  # HTTPError carries a code; everything else is dead
            code = getattr(exc, "code", None)
            if isinstance(code, int):
                status, final = code, getattr(exc, "url", None) or url
                retry_after = _retry_after_seconds(exc)
                if method == "HEAD" and code in (400, 403, 405, 501):
                    continue
                break
            status, final = None, None
    return status, final, retry_after


def check_one(
    url: str, opener: Any, *, on_rate_limit: Callable[[str], None] | None = None
) -> CheckResult:
    """HEAD first; fall back to GET when HEAD is rejected (405/403) or errors.

    A rate-limit answer (429/503) is retried with backoff — the host is telling us
    to wait, not that the page is gone.
    """
    status = final = retry_after = None
    for attempt in range(RETRY_ATTEMPTS):
        status, final, retry_after = _attempt(url, opener)
        if status not in TRANSIENT_STATUSES:
            break
        if on_rate_limit is not None:
            on_rate_limit(host_of(url))
        if attempt < RETRY_ATTEMPTS - 1:
            time.sleep(retry_after if retry_after is not None else RETRY_BACKOFF_S[attempt])
    alive, reason = classify(url, status, final)
    return CheckResult(url, status, final, alive, reason)


# --- rate limiting ---------------------------------------------------------------


class HostRateLimiter:
    """Token-ish per-host limiter: enforce a minimum interval between requests."""

    def __init__(self, min_interval: float = 1.0) -> None:
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._interval: dict[str, float] = {}
        self._lock = threading.Lock()

    def interval_for(self, host: str) -> float:
        return self._interval.get(host, self.min_interval)

    def back_off(self, host: str, factor: float = RATE_LIMIT_PENALTY) -> None:
        """A host pushed back — slow it down for the rest of the run.

        Without this, one rate-limited host keeps being hammered at the global
        pace and every subsequent URL on it comes back 429.
        """
        with self._lock:
            self._interval[host] = min(self.interval_for(host) * factor, MAX_HOST_INTERVAL_S)

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.time()
            prev = self._last.get(host, 0.0)
            sleep_for = max(0.0, self.interval_for(host) - (now - prev))
            self._last[host] = now + sleep_for
        if sleep_for > 0:
            time.sleep(sleep_for)


# --- batch driver ----------------------------------------------------------------


def dedupe_urls(urls: Iterable[str]) -> list[str]:
    """Collapse to one representative per (host, path) — kaggle dumps share a URL."""
    seen: dict[tuple[str, str], str] = {}
    for u in urls:
        try:
            p = urlparse(u)
        except Exception:
            continue
        key = (p.netloc.lower(), p.path.rstrip("/"))
        seen.setdefault(key, u)
    return list(seen.values())


def check_urls(
    urls: list[str],
    *,
    max_workers: int = 8,
    min_interval: float = 1.0,
    opener_factory: Callable[[], Any] = default_opener_factory,
    limiter: HostRateLimiter | None = None,
) -> list[CheckResult]:
    limiter = limiter or HostRateLimiter(min_interval)
    local = threading.local()

    def _get_opener() -> Any:
        op = getattr(local, "opener", None)
        if op is None:
            op = opener_factory()
            local.opener = op
        return op

    def _task(url: str) -> CheckResult:
        limiter.wait(host_of(url))
        return check_one(url, _get_opener(), on_rate_limit=limiter.back_off)

    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(_task, urls))


# --- cache -----------------------------------------------------------------------


def load_cache(path: Path = URL_CACHE_PATH) -> dict[str, dict[str, Any]]:
    """Load the cache, dropping rate-limit answers written by older runs.

    A 429/503 is not a verdict, so an entry holding one is not a cache hit —
    it is a URL we still have to check. Filtering on load heals a cache that a
    previous run poisoned (3,998 GSMArena pages were parked as dead this way,
    all of which answer 200 when asked at a civil pace).
    """
    return {
        e["url"]: e
        for e in ledger.iter_entries(path)
        if isinstance(e.get("url"), str) and e.get("status") not in TRANSIENT_STATUSES
    }


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except Exception:
        return None


def is_fresh(entry: dict[str, Any], now: datetime, ttl_days: int) -> bool:
    ts = _parse_ts(entry.get("checked_at", ""))
    if ts is None:
        return False
    return (now - ts).days < ttl_days


def save_cache(cache: dict[str, dict[str, Any]], path: Path = URL_CACHE_PATH) -> None:
    ledger.replace_all(list(cache.values()), path)


def result_to_entry(r: CheckResult, ts: str) -> dict[str, Any]:
    return {
        "url": r.url,
        "status": r.status,
        "final_url": r.final_url,
        "alive": r.alive,
        "reason": r.reason,
        "checked_at": ts,
    }


def record_liveness(source_urls: list[str], cache: dict[str, dict[str, Any]]) -> tuple[int, int]:
    """(#live, #dead) for a record's URLs that are present in the cache."""
    live = dead = 0
    for u in source_urls:
        e = cache.get(u)
        if e is None:
            continue
        if e.get("alive"):
            live += 1
        else:
            dead += 1
    return live, dead
