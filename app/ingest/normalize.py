"""Free-text catalog values → TechAPI schema-typed fields.

Wikipedia (and most vendor pages) ship values like ``"3.0 GHz"`` or
``"October 17, 2023"``; these helpers convert them to floats / ints / ISO
dates that the validator accepts. Each parser returns ``None`` on failure
so callers can mark a row as a draft instead of crashing the pipeline.
"""

from __future__ import annotations

import re
from datetime import date

_FREQ_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(GHz|MHz)\b", re.IGNORECASE)
_INT_RE = re.compile(r"(\d{1,4})")
_CACHE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(MB|KB|GB)\b", re.IGNORECASE)
_TDP_RE = re.compile(r"(\d{1,4})(?:\s*/\s*\d{1,4})?\s*W\b", re.IGNORECASE)

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_NUMERIC_DATE_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})$")
_LONG_DATE_RE = re.compile(
    r"\b("
    r"January|February|March|April|May|June|July|"
    r"August|September|October|November|December"
    r")\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE,
)
_SHORT_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+("
    r"January|February|March|April|May|June|July|"
    r"August|September|October|November|December"
    r")\s+(\d{4})\b",
    re.IGNORECASE,
)
_QUARTER_RE = re.compile(r"\bQ([1-4])\s*'?(\d{2}|\d{4})\b", re.IGNORECASE)
_YEAR_ONLY_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

_MONTHS = {
    name.lower(): index
    for index, name in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}


def parse_frequency_ghz(text: str) -> float | None:
    """``"3.0 GHz"`` → ``3.0``; ``"3200 MHz"`` → ``3.2``."""
    if not text:
        return None
    match = _FREQ_RE.search(text)
    if not match:
        return None
    value = float(match.group(1))
    return value if match.group(2).lower() == "ghz" else round(value / 1000, 3)


def parse_tdp_w(text: str) -> int | None:
    """``"65 W"`` → ``65``; ``"65/95 W"`` → ``65`` (takes the lower bound)."""
    if not text:
        return None
    match = _TDP_RE.search(text)
    return int(match.group(1)) if match else None


def parse_cache_mb(text: str) -> float | None:
    """``"30 MB"`` → ``30.0``; ``"512 KB"`` → ``0.5``; ``"1 GB"`` → ``1024.0``."""
    if not text:
        return None
    match = _CACHE_RE.search(text)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).upper()
    if unit == "MB":
        return value
    if unit == "KB":
        return round(value / 1024, 4)
    if unit == "GB":
        return value * 1024
    return None


def parse_int(text: str) -> int | None:
    """First integer token in ``text``; rejects empty/whitespace input."""
    if not text:
        return None
    match = _INT_RE.search(text)
    return int(match.group(1)) if match else None


def parse_cores_threads(text: str) -> tuple[int | None, int | None]:
    """``"8 / 16"`` → ``(8, 16)``; ``"16"`` → ``(16, 16)`` (assumes SMT)."""
    if not text:
        return (None, None)
    nums = re.findall(r"\d+", text)
    if not nums:
        return (None, None)
    if len(nums) == 1:
        cores = int(nums[0])
        return (cores, cores)
    return (int(nums[0]), int(nums[1]))


def parse_date(text: str) -> date | None:
    """Best-effort calendar date. ``"Q3 2023"`` → ``2023-07-01``; year-only → Jan 1."""
    if not text:
        return None
    stripped = text.strip()

    if (match := _ISO_DATE_RE.match(stripped)):
        year, month, day = (int(g) for g in match.groups())
        return _safe_date(year, month, day)
    if (match := _NUMERIC_DATE_RE.match(stripped)):
        year, month, day = (int(g) for g in match.groups())
        return _safe_date(year, month, day)
    if (match := _LONG_DATE_RE.search(stripped)):
        month_name, day_str, year_str = match.group(1), match.group(2), match.group(3)
        return _safe_date(int(year_str), _MONTHS[month_name.lower()], int(day_str))
    if (match := _SHORT_DATE_RE.search(stripped)):
        day_str, month_name, year_str = match.group(1), match.group(2), match.group(3)
        return _safe_date(int(year_str), _MONTHS[month_name.lower()], int(day_str))
    if (match := _QUARTER_RE.search(stripped)):
        quarter = int(match.group(1))
        year_raw = match.group(2)
        year = 2000 + int(year_raw) if len(year_raw) == 2 else int(year_raw)
        return _safe_date(year, (quarter - 1) * 3 + 1, 1)
    if (match := _YEAR_ONLY_RE.search(stripped)):
        return _safe_date(int(match.group(1)), 1, 1)
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def guess_cpu_segment(name: str) -> str:
    """Heuristic CPU segment classifier.

    Returns one of ``"desktop"``, ``"laptop"``, ``"hedt"``, or ``"server"``.
    """
    lowered = name.lower()
    if any(token in lowered for token in ("xeon", "epyc", "altra", "ampereone", "opteron")):
        return "server"
    if "threadripper" in lowered:
        return "hedt"
    # i7-13700K → desktop; i7-13700H → laptop. Look at the suffix on the model number.
    if re.search(r"\b\d{3,5}([a-z]{1,3})\b", lowered):
        match = re.search(r"\b\d{3,5}([a-z]{1,3})\b", lowered)
        suffix = match.group(1) if match else ""
        if any(letter in suffix for letter in ("h", "u", "y", "p", "m")) and "k" not in suffix:
            return "laptop"
        if suffix in {"x", "xe"}:
            return "hedt"
    return "desktop"
