"""Batched entity existence checks, using the promotion URL cache.

Identity/property cross-reference is deliberately deferred: labels may be aliases
or translations, and source dates can differ in precision or release semantics.
Existence is source liveness, not independent confirmation of a record's claims.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener

from .http_check import CheckResult

USER_AGENT = "TechEngine/0.1 (https://github.com/GetTechAPI/TechEngine; source verification)"


def qid_of(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url)
        if (parsed.scheme not in {"http", "https"}
                or parsed.netloc.lower() not in {"wikidata.org", "www.wikidata.org"}
                or parsed.query or parsed.fragment):
            return None
        match = re.fullmatch(r"/wiki/(Q[1-9][0-9]*)/?", parsed.path)
        return match[1] if match else None
    except ValueError:
        return None


def check_batches(
    urls: list[str], *, opener: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[list[CheckResult]]:
    """Yield completed batches for incremental persistence; errors stay uncached.

    No redirect resolution is requested: redirect entities are dead citations.
    maxlag/API/transport failures are indeterminate and retried next run.
    """
    grouped: dict[str, list[str]] = {}
    for url in urls:
        qid = qid_of(url)
        if qid:
            grouped.setdefault(qid, []).append(url)
    ids = list(grouped)
    opener = opener or build_opener()
    for start in range(0, len(ids), 50):
        if start:
            sleep(1.0)
        batch = ids[start:start + 50]
        params = urlencode({
            "action": "wbgetentities", "ids": "|".join(batch), "props": "info",
            "format": "json", "maxlag": "5",
        })
        request = Request(
            "https://www.wikidata.org/w/api.php?" + params,
            headers={"User-Agent": USER_AGENT},
        )
        try:
            with opener.open(request, timeout=30) as response:
                payload = json.load(response)
            if "error" in payload:
                sleep(5.0)
                yield []
                continue
            entities = payload.get("entities", {})
            results: list[CheckResult] = []
            for qid in batch:
                entity = entities.get(qid)
                if not isinstance(entity, dict):
                    continue  # incomplete/malformed response is not a dead verdict
                missing = "missing" in entity
                redirected = "redirect" in entity or entity.get("id") != qid
                if not missing and not redirected and "lastrevid" not in entity:
                    continue
                alive = not missing and not redirected
                reason = "wikidata-entity" if alive else (
                    "wikidata-missing" if missing else "wikidata-redirect"
                )
                results.extend(CheckResult(url, 200, url, alive, reason) for url in grouped[qid])
            yield results
        except (OSError, ValueError, TypeError, AttributeError):
            yield []
