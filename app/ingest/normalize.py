"""Free-text catalog values → TechAPI schema-typed fields.

Wikipedia (and most vendor pages) ship values like ``"3.0 GHz"`` or
``"October 17, 2023"``; these helpers convert them to floats / ints / ISO
dates that the validator accepts. Each parser returns ``None`` on failure
so callers can mark a row as a draft instead of crashing the pipeline.
"""

from __future__ import annotations

import math
import re
from datetime import date

_FREQ_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(GHz|MHz)\b", re.IGNORECASE)
_FREQ_MHZ_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(MHz|GHz)\b", re.IGNORECASE)
_INT_RE = re.compile(r"(\d{1,5})")
_CACHE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(MB|KB|GB)\b", re.IGNORECASE)
_MEMORY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(GB|MB)\b", re.IGNORECASE)
_BUS_RE = re.compile(r"(\d{2,4})\s*-?\s*bit\b", re.IGNORECASE)
_PCIE_RE = re.compile(r"PCI[-\s]?[Ee]?\s*(?:Gen\s*)?(\d(?:\.\d)?)", re.IGNORECASE)
_TDP_RE = re.compile(
    r"(?<![\d.])(\d{1,4}(?:\.\d+)?)(?:\s*/\s*\d{1,4}(?:\.\d+)?)?\s*W\b", re.IGNORECASE
)
# "12–25 W": a cTDP range, which names no default TDP.
_TDP_RANGE_RE = re.compile(
    r"(?<![\d.])\d{1,4}(?:\.\d+)?\s*(?:W\s*)?(?:–|—|−|-|to)\s*\d{1,4}(?:\.\d+)?\s*W\b",
    re.IGNORECASE,
)
_FREQ_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:–|—|-|to)\s*(\d+(?:\.\d+)?)\s*(GHz|MHz)\b", re.IGNORECASE
)
_RAM_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(GB|MB)\b", re.IGNORECASE)
_BATTERY_RE = re.compile(r"(\d{3,5})\s*m\s*A\s*h\b", re.IGNORECASE)
_WEIGHT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*g\b")
_OS_VERSION_RE = re.compile(r"\b(\d{1,2}(?:\.\d+)?)\b")

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_NUMERIC_DATE_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})$")
# Full month names and common abbreviations ("Mar", "Sept.") — GPU list tables
# write dates like "Mar 11, 2010" and "Dec 2024".
_MONTH_NAME = (
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
_LONG_DATE_RE = re.compile(
    r"\b" + _MONTH_NAME + r"\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE,
)
_SHORT_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+" + _MONTH_NAME + r",?\s+(\d{4})\b",
    re.IGNORECASE,
)
_QUARTER_RE = re.compile(r"\bQ([1-4])\s*'?(\d{2}|\d{4})\b", re.IGNORECASE)
# "1H 2025" and "H2 2024".
_HALF_YEAR_RE = re.compile(r"\b(?:([12])H|H([12]))\s*(\d{4})\b", re.IGNORECASE)
_MONTH_YEAR_RE = re.compile(r"\b" + _MONTH_NAME + r",?\s+(\d{4})\b", re.IGNORECASE)
_YEAR_ONLY_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

_MONTHS = {
    name.lower()[:3]: index
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
    """``"65 W"`` → ``65``; ``"65/95 W"`` → ``65``; ``"9.5 W"`` → ``10`` (half-up).

    ``"45 W (35–54 W)"`` → ``45``: the default precedes its cTDP range. A cell
    that is only a range (``"12–25 W"``) states no default, so it gives ``None``;
    neither end of the range is the part's TDP. Two bare numbers before the
    unit (``"92.6 68 W"``) are two parts' values and give ``None`` too.
    """
    if not text:
        return None
    cleaned = _TDP_RANGE_RE.sub(" ", text)
    match = _TDP_RE.search(cleaned)
    if match is None or re.search(r"\d\s+$", cleaned[: match.start()]):
        return None  # "92.6 68 W": one value per OPN, so neither is the part's
    return math.floor(float(match.group(1)) + 0.5)


def parse_frequency_range_ghz(text: str) -> tuple[float, float] | None:
    """``"1.7–2.0 GHz"`` → ``(1.7, 2.0)`` (base, boost); ``None`` if not a range."""
    if not text:
        return None
    match = _FREQ_RANGE_RE.search(text)
    if not match:
        return None
    low, high = float(match.group(1)), float(match.group(2))
    if match.group(3).lower() == "mhz":
        low, high = round(low / 1000, 3), round(high / 1000, 3)
    return (low, high) if low < high else None


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
    """``"8 / 16"`` → ``(8, 16)``; ``"16"`` → ``(16, None)``.

    A bare core count says nothing about SMT, so threads stay unknown rather
    than guessed (Atom/Xeon tables list "Cores" only; many of those parts have HT).
    """
    if not text:
        return (None, None)
    nums = re.findall(r"\d+", text)
    if not nums:
        return (None, None)
    if len(nums) == 1:
        return (int(nums[0]), None)
    return (int(nums[0]), int(nums[1]))


def parse_date(text: str) -> date | None:
    """Best-effort calendar date. ``"Q3 2023"`` → ``2023-07-01``; year-only → Jan 1."""
    period = parse_date_period(text)
    return period[0] if period else None


# A quarter is the longest period whose first day still stands for the date.
_LONGEST_DATED_PERIOD_DAYS = 92


def release_date_of(period: tuple[date, date] | None) -> date | None:
    """Release date a collector may write for ``period``, or ``None``.

    A day, a month or a quarter gives its first day, as the curated records
    do. A half-year or a bare year gives ``None``: January 1 would be a guess.
    """
    if period is None:
        return None
    first, last = period
    return first if (last - first).days < _LONGEST_DATED_PERIOD_DAYS else None


def parse_date_period(text: str) -> tuple[date, date] | None:
    """``(first, last)`` day of the period a date cell names.

    A full date is a one-day period. ``"Q4 2026"`` is Oct 1 – Dec 31, ``"Dec 2024"``
    the whole month, ``"1H 2025"`` Jan 1 – Jun 30 and a bare year the whole year,
    so a caller can tell whether the period had ended (the part had shipped) by a
    given day.
    """
    if not text:
        return None
    stripped = text.strip()

    if (match := _ISO_DATE_RE.match(stripped)):
        year, month, day = (int(g) for g in match.groups())
        return _day(_safe_date(year, month, day))
    if (match := _NUMERIC_DATE_RE.match(stripped)):
        year, month, day = (int(g) for g in match.groups())
        return _day(_safe_date(year, month, day))
    if (match := _LONG_DATE_RE.search(stripped)):
        month_name, day_str, year_str = match.group(1), match.group(2), match.group(3)
        return _day(_safe_date(int(year_str), _MONTHS[month_name.lower()[:3]], int(day_str)))
    if (match := _SHORT_DATE_RE.search(stripped)):
        day_str, month_name, year_str = match.group(1), match.group(2), match.group(3)
        return _day(_safe_date(int(year_str), _MONTHS[month_name.lower()[:3]], int(day_str)))
    if (match := _QUARTER_RE.search(stripped)):
        quarter = int(match.group(1))
        year_raw = match.group(2)
        year = 2000 + int(year_raw) if len(year_raw) == 2 else int(year_raw)
        return _months(year, (quarter - 1) * 3 + 1, 3)
    if (match := _HALF_YEAR_RE.search(stripped)):
        half = int(match.group(1) or match.group(2))
        return _months(int(match.group(3)), 1 if half == 1 else 7, 6)
    if (match := _MONTH_YEAR_RE.search(stripped)):
        return _months(int(match.group(2)), _MONTHS[match.group(1).lower()[:3]], 1)
    if (match := _YEAR_ONLY_RE.search(stripped)):
        return _months(int(match.group(1)), 1, 12)
    return None


def _day(day: date | None) -> tuple[date, date] | None:
    return (day, day) if day else None


def _months(year: int, first_month: int, count: int) -> tuple[date, date] | None:
    first = _safe_date(year, first_month, 1)
    if first is None:
        return None
    last_month = first_month + count - 1
    after = date(year + 1, 1, 1) if last_month == 12 else date(year, last_month + 1, 1)
    return first, date.fromordinal(after.toordinal() - 1)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_frequency_mhz(text: str) -> int | None:
    """``"1500 MHz"`` → ``1500``; ``"2.5 GHz"`` → ``2500``."""
    if not text:
        return None
    match = _FREQ_MHZ_RE.search(text)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    return int(value * 1000) if unit == "ghz" else int(value)


def parse_memory_gb(text: str) -> float | None:
    """``"24 GB"`` → ``24.0``; ``"4096 MB"`` → ``4.0``."""
    if not text:
        return None
    match = _MEMORY_RE.search(text)
    if not match:
        return None
    value = float(match.group(1))
    return value if match.group(2).lower() == "gb" else round(value / 1024, 3)


def mib_to_gb(mib: float) -> float | None:
    """MiB → GB as the dataset stores it (binary): ``32`` → ``0.03125``.

    Unrounded, so power-of-two sizes stay exact. 1 MiB and less gives ``None``:
    0.0009765625 is under the validator's 0.001 GB floor.
    """
    return mib / 1024 if mib > 1 else None


def parse_memory_bus_bit(text: str) -> int | None:
    """``"384-bit"`` → ``384``; ``"128 bit"`` → ``128``."""
    if not text:
        return None
    match = _BUS_RE.search(text)
    return int(match.group(1)) if match else None


def parse_pcie_version(text: str) -> str | None:
    """``"PCIe 4.0 x16"`` → ``"4.0"``; ``"PCI-e Gen 5"`` → ``"5"``."""
    if not text:
        return None
    match = _PCIE_RE.search(text)
    return match.group(1) if match else None


def guess_gpu_segment(name: str) -> str:
    """Heuristic GPU segment classifier (``"consumer"`` vs ``"enterprise"``)."""
    lowered = name.lower()
    enterprise_tokens = (
        "quadro", "tesla", "a100", "h100", "h200", "b100", "b200",
        "instinct", "mi300", "mi325", "mi350",
        "data center", "datacenter", "professional", "radeon pro",
        "rtx 6000", "rtx 5000", "rtx 4500", "rtx 4000",
        # Workstation lines, matching where the curated records file them.
        "firepro", "nvs ", "rtx pro",
    )
    if any(token in lowered for token in enterprise_tokens):
        return "enterprise"
    return "consumer"


def parse_ram_gb(text: str) -> int | None:
    """``"8 GB"`` → ``8``; ``"512 MB"`` → ``None`` (sub-GB ignored).

    Picks the largest value when the text lists multiple options like
    ``"6/8/12 GB"`` to reflect the flagship configuration.
    """
    if not text:
        return None
    values: list[int] = []
    for match in _RAM_RE.finditer(text):
        amount = float(match.group(1))
        unit = match.group(2).lower()
        if unit == "gb" and amount >= 1:
            values.append(int(amount))
    return max(values) if values else None


def parse_battery_mah(text: str) -> int | None:
    """``"5,000 mAh"`` → ``5000``; rejects values outside [500, 12000]."""
    if not text:
        return None
    match = _BATTERY_RE.search(text.replace(",", ""))
    if not match:
        return None
    value = int(match.group(1))
    return value if 500 <= value <= 12000 else None


def parse_weight_g(text: str) -> int | None:
    """``"232 g"`` → ``232``; rejects values outside [50, 500]."""
    if not text:
        return None
    match = _WEIGHT_RE.search(text.replace(",", ""))
    if not match:
        return None
    value = int(float(match.group(1)))
    return value if 50 <= value <= 500 else None


def guess_os(text: str, *, brand: str = "") -> str | None:
    """Best-effort OS string from a Wikipedia smartphone row.

    Recognizes Android/iOS/iPadOS/HarmonyOS/Windows; falls back to inferring
    ``"iOS"`` for Apple brand, ``"Android"`` for everyone else, when the
    text is non-empty but does not name an OS.
    """
    if not text:
        return None
    lowered = text.lower()
    # OS names first; OEM-skin names are fallback because rows usually print
    # the OS *and* the skin together (e.g. "Android 14, One UI 6.1") and the
    # underlying OS is the schema-relevant value.
    for token, label in (
        ("ipados", "iPadOS"),
        ("ios", "iOS"),
        ("android", "Android"),
        ("harmonyos", "HarmonyOS"),
        ("windows phone", "Windows Phone"),
        ("windows", "Windows"),
        ("hyperos", "HyperOS"),
        ("oxygenos", "OxygenOS"),
        ("oneui", "One UI"),
        ("one ui", "One UI"),
    ):
        if token in lowered:
            version = _OS_VERSION_RE.search(text)
            return f"{label} {version.group(1)}" if version else label
    if brand == "apple":
        return "iOS"
    if brand:
        return "Android"
    return None


def normalize_socket(text: str) -> str | None:
    """Socket in the model doc's form: ``"LGA 1151"`` → ``"LGA1151"``,
    ``"Socket AM4"`` → ``"AM4"``, ``"F"`` → ``"F (1207)"``."""
    value = " ".join(text.split())
    value = re.sub(r"^Socket\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^(FC)?LGA\s+(?=\d)", r"\1LGA", value, flags=re.IGNORECASE)
    if value == "F":
        value = "F (1207)"
    return value or None


def guess_cpu_segment(name: str) -> str:
    """Heuristic CPU segment classifier.

    Returns one of ``"desktop"``, ``"laptop"``, ``"hedt"``, or ``"server"``.
    """
    lowered = name.lower()
    if any(token in lowered for token in ("xeon", "epyc", "altra", "ampereone", "opteron")):
        return "server"
    if "threadripper" in lowered:
        return "hedt"
    # Atom lines by Intel's own naming: C/P = microserver & network SoCs
    # (Avoton, Rangeley, Denverton, Snow Ridge, Parker Ridge); Z/N/x3/x5/x7 =
    # tablet/netbook/phone; D = desktop (Pineview); E/x6000 embedded fall
    # through to desktop.
    if (atom := re.search(r"\batom\s+([a-z]+)\d", lowered)) is not None:
        line = atom.group(1)
        if line in {"c", "p"}:
            return "server"
        if line in {"z", "n", "x"} and not re.search(r"\batom\s+x6\d{3}", lowered):
            return "laptop"
        return "desktop"
    # i7-13700K → desktop; i7-13700H → laptop. Look at the suffix on the model number.
    if re.search(r"\b\d{3,5}([a-z]{1,3})\b", lowered):
        match = re.search(r"\b\d{3,5}([a-z]{1,3})\b", lowered)
        suffix = match.group(1) if match else ""
        if any(letter in suffix for letter in ("h", "u", "y", "p", "m")) and "k" not in suffix:
            return "laptop"
        # Core X/XE parts are HEDT; Ryzen X parts are ordinary desktop chips.
        if suffix in {"x", "xe"} and re.search(r"\bcore\b", lowered):
            return "hedt"
    return "desktop"
