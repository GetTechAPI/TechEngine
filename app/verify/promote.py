"""Tier 3 — hybrid escalation + safe ``verified:true`` write-back.

Promotion rules (only ever ``false -> true``, never a demotion):
* band green AND >=1 cited source is a *live* Tier-1 host  -> auto-promote
* Tier 2 cross-reference returned ``confirm`` (exact heading) -> promote
* otherwise stay unverified, with a logged reason

Write-back is *surgical*: only the ``"verified": false`` token is rewritten to
``true`` in the raw bytes. Full re-serialization is intentionally avoided because
the seed files keep short arrays inline (``[64, 128, 256]``) while ``json.dumps``
would expand them, producing a huge spurious diff and defeating the "only verified
changed" guard. Edits are atomic (temp file + ``os.replace``) and preserve LF.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, NamedTuple

from . import hosts, http_check
from .common import STATE_DIR

CROSSREF_CACHE_PATH = STATE_DIR / "crossref_cache.jsonl"

# A top-level, one-key-per-line "verified": false entry (2-space indented).
# Permit either common newline convention.  With ``re.MULTILINE``, ``$`` sits
# before ``\n`` but after the preceding ``\r`` in CRLF files.
_VERIFIED_FALSE_RE = re.compile(r'^(  )"verified": false(,?)[ \t]*(\r?)$', re.MULTILINE)


class PromotionDecision(NamedTuple):
    promote: bool
    reason: str


def has_live_authoritative_source(
    source_urls: list[str], url_cache: dict[str, dict[str, Any]]
) -> bool:
    """True if an authoritative cited URL has a usable liveness signal.

    A normal alive response always qualifies. An explicit anti-bot challenge also
    qualifies, but only for an already classified Tier-1/Tier-2 host: it proves
    the request reached that host while the gateway withheld page inspection.
    This preserves the live-source gate without treating every 403 as a live
    record. Generic errors, ordinary 401/403, redirects to a homepage, and
    unclassified hosts remain insufficient for promotion.

    This distinction is deliberate. The September 2026 probe found Geekbench's
    Cloudflare challenge under the verifier User-Agent, while several other
    Tier-2 databases returned normal 200 responses. Treating challenge responses
    as dead made a host-wide automation policy look like thousands of dead
    citations; dropping the liveness gate altogether made unchecked citations
    promotable. This narrow fallback avoids both failure modes.
    """
    for u in source_urls:
        entry = url_cache.get(u)
        if not entry or hosts.tier_of_host(hosts.host_of(u)) not in (1, 2):
            continue
        if entry.get("alive") or http_check.is_automation_challenge(entry):
            return True
    return False


# Backwards-compatible alias (older callers/tests).
has_live_t1 = has_live_authoritative_source


def decide(
    *, band: str, source_urls: list[str], url_cache: dict[str, dict[str, Any]],
    crossref_decision: str | None,
) -> PromotionDecision:
    # Reality veto: if an authoritative external source contradicts the record's
    # specs (e.g. release year mismatch), never promote — even a green record.
    # Accuracy must be reality-based; that's the whole point of verification.
    if crossref_decision == "contradict":
        return PromotionDecision(False, "crossref-contradict")
    # Reality confirm: external source agrees -> strongest promotion.
    if crossref_decision == "confirm":
        return PromotionDecision(True, "crossref-confirm")
    # green is only an offline candidate. Promotion still requires a cited
    # authoritative source to have been confirmed alive by Tier 1.
    if band == "green" and has_live_authoritative_source(source_urls, url_cache):
        return PromotionDecision(True, "green-live-source")
    return PromotionDecision(False, "needs-confirmation")


# --- surgical write-back ---------------------------------------------------------


def flip_verified_text(raw: str) -> str | None:
    """Return ``raw`` with a single top-level ``verified:false`` flipped to true.

    Returns None (refuse) unless exactly one such token exists, so we never touch
    a record that isn't shaped the way we expect.
    """
    new, n = _VERIFIED_FALSE_RE.subn(r'\g<1>"verified": true\g<2>\g<3>', raw)
    return new if n == 1 else None


def write_verified_true(abs_path: Path) -> bool:
    """Atomically flip verified false->true in a seed file. Returns True if written."""
    raw = abs_path.read_bytes().decode("utf-8")
    new = flip_verified_text(raw)
    if new is None:
        return False
    tmp = abs_path.with_suffix(abs_path.suffix + ".tmp")
    tmp.write_bytes(new.encode("utf-8"))
    os.replace(tmp, abs_path)
    return True


def load_crossref_cache(path: Path = CROSSREF_CACHE_PATH) -> dict[tuple[str, str], dict[str, Any]]:
    from . import ledger
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for e in ledger.iter_entries(path):
        cat, slug = e.get("category"), e.get("slug")
        if isinstance(cat, str) and isinstance(slug, str):
            out[(cat, slug)] = e
    return out
