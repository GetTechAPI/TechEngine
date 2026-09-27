"""Per-category cross-field consistency predicates (pure functions).

The structural validator only range-checks single fields. These predicates check
*relations between fields* — the kind of contradiction that means a record cannot
describe a real part (threads < cores, a chip that postdates the device it powers,
a clock that boosts below its base). Each predicate yields a :class:`Signal`.

Severity:
* ``hard`` — logically impossible. Forces the record's band to red regardless of score.
* soft  — implausible but physically possible; only subtracts from the score.

``NA`` results (inputs absent) are neither pass nor fail and never penalize.
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, NamedTuple
from urllib.parse import urlparse

from .wikidata import qid_of

# Range table mirrored from app.validate's _check_range call sites, keyed by
# (category, field) -> (lo, hi). A parity smoke test asserts this stays in sync.
RANGES: dict[tuple[str, str], tuple[float, float]] = {
    ("brand", "founded_year"): (1800, 2100),
    ("soc", "process_nm"): (1.0, 100.0),
    ("smartphone", "ram_gb"): (1, 64),
    ("smartphone", "battery_mah"): (500, 12000),
    ("smartphone", "weight_g"): (50, 500),
    ("smartphone", "msrp_usd"): (50, 5000),
    ("mobile", "ram_gb"): (0.016, 64),
    ("mobile", "battery_mah"): (50, 20000),
    ("mobile", "weight_g"): (10, 2000),
    ("mobile", "msrp_usd"): (10, 10000),
    ("gpu", "memory_gb"): (0.001, 512),
    ("gpu", "tdp_w"): (1, 3000),
    ("gpu", "msrp_usd"): (50, 100000),
    ("cpu", "cores"): (1, 512),
    ("cpu", "threads"): (1, 1024),
    ("cpu", "msrp_usd"): (20, 50000),
}

_RESOLUTION_RE = re.compile(r"(\d{2,5})\s*[x×]\s*(\d{2,5})")
_ANDROID_RE = re.compile(r"android\s*(\d{1,2})", re.IGNORECASE)

# Earliest plausible release year for a given Android major version (release-vs-era).
_ANDROID_MIN_YEAR: dict[int, int] = {
    4: 2011, 5: 2014, 6: 2015, 7: 2016, 8: 2017, 9: 2018,
    10: 2019, 11: 2020, 12: 2021, 13: 2022, 14: 2023, 15: 2024, 16: 2025,
}


class Signal(NamedTuple):
    name: str
    result: str  # "pass" | "fail" | "na"
    hard: bool = False

    @property
    def failed(self) -> bool:
        return self.result == "fail"


def _num(value: Any) -> float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _cmp_ge(name: str, a: Any, b: Any, *, hard: bool) -> Signal:
    """``a >= b`` when both present, else NA."""
    x, y = _num(a), _num(b)
    if x is None or y is None:
        return Signal(name, "na", hard)
    return Signal(name, "pass" if x >= y else "fail", hard)


def _year_of(value: Any) -> int | None:
    if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return None


# A bulk-imported record with no known day is stored as January 1st. The year on
# such a date is an approximation, not a measurement.
_PLACEHOLDER_SOC_YEAR_SLACK = 2


def _is_placeholder_date(value: Any) -> bool:
    return isinstance(value, str) and value[5:10] == "01-01"


def parse_resolution(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    m = _RESOLUTION_RE.search(value)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _release_not_future(rec: dict[str, Any], now_year: int) -> Signal:
    y = _year_of(rec.get("release_date"))
    if y is None:
        return Signal("release_not_future", "na", hard=True)
    return Signal("release_not_future", "pass" if y <= now_year + 1 else "fail", hard=True)


# --- per-category predicate sets -------------------------------------------------


def cpu_signals(rec: dict[str, Any], now_year: int) -> list[Signal]:
    out = [
        _cmp_ge("threads_ge_cores", rec.get("threads"), rec.get("cores"), hard=True),
        _cmp_ge("boost_ge_base", rec.get("boost_clock_ghz"), rec.get("base_clock_ghz"), hard=True),
        _cmp_ge("max_tdp_ge_tdp", rec.get("max_tdp_w"), rec.get("tdp_w"), hard=False),
        # No passmark_cpu_mark vs passmark_single check: PassMark's CPU Mark and
        # its Single Thread Rating are separately normalised scales, so their
        # magnitudes are not comparable. The old check read that as a defect —
        # it failed 51 of 51 single-core parts, and its failures tracked absolute
        # weakness rather than parallelism (failing median CPU Mark 641 vs 19,657
        # for passing), with near-ties at the boundary (Core 2 Duo E8600: 1378 vs
        # 1388). It only ever "held" because modern CPU Marks are large.
        # Cinebench and Geekbench below DO report both figures on one scale, so
        # multi >= single is a real expectation there.
        _cmp_ge("cb23_multi_ge_single", rec.get("cinebench_r23_multi"),
                rec.get("cinebench_r23_single"), hard=False),
        _cmp_ge("gb_multi_ge_single", rec.get("geekbench_multi"),
                rec.get("geekbench_single"), hard=False),
        _release_not_future(rec, now_year),
    ]
    # p_cores + e_cores == cores (hybrid parts), only when both core splits given.
    p, e, c = _num(rec.get("p_cores")), _num(rec.get("e_cores")), _num(rec.get("cores"))
    if p is not None and e is not None and c is not None:
        out.append(Signal("hybrid_core_sum", "pass" if p + e == c else "fail", hard=False))
    else:
        out.append(Signal("hybrid_core_sum", "na", hard=False))
    return out


def gpu_signals(rec: dict[str, Any], now_year: int) -> list[Signal]:
    out = [
        _cmp_ge("boost_ge_base", rec.get("boost_clock_mhz"), rec.get("base_clock_mhz"), hard=True),
        _release_not_future(rec, now_year),
    ]
    # The core count belongs in the vendor's own field: nvidia -> cuda_cores,
    # amd/intel -> stream_processors. Carrying the OTHER vendor's field is a
    # contradiction and is flagged. Carrying NEITHER is not: it is an absence,
    # which `completeness` already scores (both fields are gpu RICH_FIELDS), and
    # scoring it here too charged the same gap twice under a name that claims the
    # record disagrees with itself. It also misread pre-unified-shader parts —
    # an NV1 or a RIVA 128 predates the concept of a CUDA core, so the field is
    # inapplicable rather than missing. Of 281 records flagged before this
    # change, 279 were plain gaps and 2 were real vendor mismatches.
    mfr = str(rec.get("manufacturer") or "").lower()
    cuda, stream = _num(rec.get("cuda_cores")), _num(rec.get("stream_processors"))
    if mfr == "nvidia":
        own, foreign = cuda, stream
    elif mfr in {"amd", "intel"}:
        own, foreign = stream, cuda
    else:
        own, foreign = (cuda if cuda is not None else stream), None
    if own is not None:
        result = "pass"
    elif foreign is not None:
        result = "fail"  # the count is filed under the wrong vendor's field
    else:
        result = "na"
    out.append(Signal("vendor_core_field", result, hard=False))
    # RT / Tensor cores only plausible on post-2018 (Turing / RDNA2) parts.
    y = _year_of(rec.get("release_date"))
    rt = _num(rec.get("rt_cores"))
    if rt is not None and rt > 0 and y is not None:
        out.append(Signal("rt_cores_era", "pass" if y >= 2018 else "fail", hard=False))
    else:
        out.append(Signal("rt_cores_era", "na", hard=False))
    return out


def _ppi_signal(display: dict[str, Any]) -> Signal:
    size = _num(display.get("size_inch"))
    ppi = _num(display.get("ppi"))
    res = parse_resolution(display.get("resolution"))
    if size is None or ppi is None or res is None or size <= 0:
        return Signal("ppi_consistent", "na", hard=False)
    w, h = res
    computed = math.hypot(w, h) / size
    ok = abs(computed - ppi) <= 0.15 * ppi
    return Signal("ppi_consistent", "pass" if ok else "fail", hard=False)


def _storage_signal(rec: dict[str, Any]) -> Signal:
    vals = rec.get("storage_options_gb")
    if not isinstance(vals, list) or not vals:
        return Signal("storage_sane", "na", hard=False)
    nums = [v for v in vals if isinstance(v, int) and not isinstance(v, bool)]
    if len(nums) != len(vals):
        return Signal("storage_sane", "fail", hard=False)
    ok = all(v >= 1 for v in nums) and len(set(nums)) == len(nums) and nums == sorted(nums)
    return Signal("storage_sane", "pass" if ok else "fail", hard=False)


def _android_era_signal(rec: dict[str, Any]) -> Signal:
    text = f"{rec.get('os') or ''} {rec.get('os_version') or ''}"
    m = _ANDROID_RE.search(text)
    y = _year_of(rec.get("release_date"))
    if not m or y is None:
        return Signal("os_era", "na", hard=False)
    major = int(m.group(1))
    min_year = _ANDROID_MIN_YEAR.get(major)
    if min_year is None:
        return Signal("os_era", "na", hard=False)
    return Signal("os_era", "pass" if y >= min_year else "fail", hard=False)


def mobile_signals(
    rec: dict[str, Any], now_year: int, soc_release: dict[str, str]
) -> list[Signal]:
    """Shared by smartphone / tablet / watch / pda."""
    raw_display = rec.get("display")
    display: dict[str, Any] = raw_display if isinstance(raw_display, dict) else {}
    out = [
        _ppi_signal(display),
        _storage_signal(rec),
        _android_era_signal(rec),
        _release_not_future(rec, now_year),
    ]
    # ram_gb <= max(storage_options_gb)
    ram = _num(rec.get("ram_gb"))
    vals = rec.get("storage_options_gb")
    if ram is not None and isinstance(vals, list) and vals:
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums:
            out.append(Signal("ram_le_storage", "pass" if ram <= max(nums) else "fail", hard=False))
        else:
            out.append(Signal("ram_le_storage", "na", hard=False))
    else:
        out.append(Signal("ram_le_storage", "na", hard=False))
    # SoC should not postdate the device it powers. SOFT, not hard: the dataset's
    # SoC release_dates are largely placeholder "YYYY-01-01" values that skew late
    # (e.g. Snapdragon 888 stored as 2022-01-01), so a mismatch usually means the
    # *SoC* record's date is wrong, not the device. We flag + penalize but don't
    # force-red the device on the strength of a second record's bad date.
    #
    # 92.9% of SoC records (1,954/2,104) carry a placeholder date, and their year
    # is itself imprecise by up to ~2 years, so comparing years exactly against
    # one measures the placeholder, not the device: it fails 5,488 otherwise-sound
    # phones. Where the SoC date is a placeholder we only fail a gross mismatch;
    # a real, day-precise SoC date is still compared exactly.
    soc = rec.get("soc")
    soc_date = soc_release.get(soc) if isinstance(soc, str) else None
    dev_year = _year_of(rec.get("release_date"))
    soc_year = _year_of(soc_date)
    if dev_year is not None and soc_year is not None:
        slack = _PLACEHOLDER_SOC_YEAR_SLACK if _is_placeholder_date(soc_date) else 0
        ok = soc_year <= dev_year + slack
        out.append(Signal("soc_not_after_device", "pass" if ok else "fail", hard=False))
    else:
        out.append(Signal("soc_not_after_device", "na", hard=False))
    return out


def soc_signals(rec: dict[str, Any], now_year: int) -> list[Signal]:
    out = [_release_not_future(rec, now_year)]
    # process_nm vs era: no sub-7nm before 2017, no sub-3nm before 2022 (coarse guard).
    nm = _num(rec.get("process_nm"))
    y = _year_of(rec.get("release_date"))
    if nm is not None and y is not None:
        too_advanced = (nm < 7 and y < 2017) or (nm < 3 and y < 2022)
        out.append(Signal("process_nm_era", "fail" if too_advanced else "pass", hard=False))
    else:
        out.append(Signal("process_nm_era", "na", hard=False))
    gpu_name = rec.get("gpu_name")
    out.append(
        Signal(
            "gpu_name_present",
            "pass" if isinstance(gpu_name, str) and gpu_name.strip() else "fail",
            hard=False,
        )
    )
    return out


def brand_signals(rec: dict[str, Any], now_year: int) -> list[Signal]:
    fy = _num(rec.get("founded_year"))
    if fy is None:
        founded = Signal("founded_not_future", "na", hard=False)
    else:
        founded = Signal("founded_not_future", "pass" if fy <= now_year else "fail", hard=False)
    return [founded]


def _positive_range(name: str, value: Any, lo: float, hi: float) -> Signal:
    """Missing measurements are NA; impossible ones are hard, outliers soft."""
    if value is None:
        return Signal(name, "na")
    number = _num(value)
    if number is None:
        return Signal(name, "fail")
    if not math.isfinite(number) or number <= 0:
        return Signal(name, "fail", hard=True)
    return Signal(name, "pass" if lo <= number <= hi else "fail")


def _resolution_signal(value: Any) -> Signal:
    if value in (None, ""):
        return Signal("resolution_parses", "na")
    parsed = parse_resolution(value)
    if parsed is None:
        return Signal("resolution_parses", "fail")
    if min(parsed) <= 0:
        return Signal("resolution_parses", "fail", hard=True)
    return Signal("resolution_parses", "pass")


def _device_release_signal(rec: dict[str, Any], now_year: int) -> Signal:
    # Future dates may describe announced products, so flag rather than force red.
    year = _year_of(rec.get("release_date"))
    if year is None:
        return Signal("release_not_future", "na")
    return Signal("release_not_future", "pass" if year <= now_year else "fail")


def laptop_signals(rec: dict[str, Any], now_year: int) -> list[Signal]:
    raw_display = rec.get("display")
    display = raw_display if isinstance(raw_display, dict) else {}
    return [
        _positive_range("ram_plausible", rec.get("ram_gb"), 1, 256),
        _positive_range("storage_plausible", rec.get("storage_gb"), 1, 16384),
        _positive_range("display_size_plausible", display.get("size_inch"), 7, 21),
        _positive_range("weight_plausible", rec.get("weight_g"), 400, 6000),
        _resolution_signal(display.get("resolution")),
        _ppi_signal(display),
        _device_release_signal(rec, now_year),
    ]


def monitor_signals(rec: dict[str, Any], now_year: int) -> list[Signal]:
    # Includes 7-inch touch monitors and 86-inch signage in the seed dataset.
    return [
        _positive_range("display_size_plausible", rec.get("size_inch"), 7, 86),
        _positive_range("refresh_plausible", rec.get("refresh_hz"), 24, 600),
        _resolution_signal(rec.get("resolution")),
        _ppi_signal(rec),
        _device_release_signal(rec, now_year),
    ]


def _digital_date(rec: dict[str, Any], field: str, now_year: int, earliest: int) -> Signal:
    value = rec.get(field)
    name = f"{field}_plausible"
    if value in (None, ""):
        return Signal(name, "na")
    try:
        parsed = date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None:
        return Signal(name, "fail", hard=True)
    # Imported dates can describe the publisher's founding (290 websites predate
    # the Web), or a planned release. These are ambiguous, not impossibilities.
    return Signal(name, "pass" if earliest <= parsed.year <= now_year else "fail")


def digital_signals(category: str, rec: dict[str, Any], now_year: int) -> list[Signal]:
    urls = rec.get("source_urls")
    has_qid = isinstance(urls, list) and any(qid_of(u) for u in urls)
    out = [Signal("wikidata_qid_source", "pass" if has_qid else "fail")]
    fields = ("release_date",) if category == "software" else (
        "launch_date", "release_date", "founded_date",
    )
    for field in fields:
        earliest = 1950 if category == "software" else (1800 if field == "founded_date" else 1989)
        out.append(_digital_date(rec, field, now_year, earliest))
    if category == "software":
        for field in ("developers", "operating_systems", "licenses", "genres"):
            value = rec.get(field)
            valid = isinstance(value, list) and bool(value) and all(
                isinstance(v, str) and bool(v.strip()) for v in value
            )
            out.append(Signal(f"{field}_string_list", "na" if value is None else (
                "pass" if valid else "fail"
            )))
    else:
        value = rec.get("homepage_url")
        try:
            parsed = urlparse(value) if isinstance(value, str) else None
            valid = isinstance(value, str) and parsed is not None and parsed.scheme in {
                "http", "https",
            } and bool(
                parsed.hostname
            ) and not any(c.isspace() for c in value)
        except ValueError:
            valid = False
        out.append(Signal("homepage_http_url", "na" if value is None else (
            "pass" if valid else "fail"
        )))
    return out


def signals_for(
    category: str, rec: dict[str, Any], now_year: int, soc_release: dict[str, str]
) -> list[Signal]:
    if category in {"software", "website"}:
        return digital_signals(category, rec, now_year)
    if category == "laptop":
        return laptop_signals(rec, now_year)
    if category == "monitor":
        return monitor_signals(rec, now_year)
    if category == "cpu":
        return cpu_signals(rec, now_year)
    if category == "gpu":
        return gpu_signals(rec, now_year)
    if category == "soc":
        return soc_signals(rec, now_year)
    if category == "brand":
        return brand_signals(rec, now_year)
    if category in {"smartphone", "tablet", "watch", "pda"}:
        return mobile_signals(rec, now_year, soc_release)
    return []
