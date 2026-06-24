"""Smartphone scoring (§8).

Performance is **benchmark-only** (from the SoC's Geekbench/AnTuTu, gated → ``None``
when the SoC has no benchmark). Camera/battery/display are inherently spec-derived
(there is no benchmark for them) and keep their established formulas. A hybrid
``perf`` view adds the within-generation tier/percentile for the compute axis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.models.smartphone import Smartphone
from app.models.soc import SoC
from app.services.scoring.common import (
    Hybrid,
    ReferenceScale,
    StatsLike,
    capability,
    combine,
    era_band,
    with_relative,
)
from app.services.scoring.config import ScoringConfig, load_config

CATEGORY = "phone"


@dataclass(slots=True)
class PhoneScore:
    algorithm_version: str
    overall: float | None
    performance: float | None  # == perf.index (back-compat with ScoreRead + site bars)
    camera: float | None
    battery: float | None
    display: float | None
    value: float | None
    perf: Hybrid


def _coalesce(value: float | None, default: float) -> float:
    return default if value is None else value


def _perf(
    phone: Smartphone, soc: SoC, cfg: ScoringConfig, era: str | None, stats: StatsLike | None
) -> Hybrid:
    scales = cfg.reference_scales[CATEGORY]
    weights = cfg.weights["phone_perf"]
    single = capability(soc.geekbench_single, scales["geekbench_single"])
    multi = capability(soc.geekbench_multi, scales["geekbench_multi"])
    system = capability(soc.antutu_score, scales["antutu_score"])
    if single is None and multi is None and system is None:
        return Hybrid(era=era)  # benchmark-only gate: RAM alone never yields a perf score
    ram = capability(float(phone.ram_gb), scales["ram_gb"])
    index = combine(
        [
            (single, weights["single"]),
            (multi, weights["multi"]),
            (system, weights["system"]),
            (ram, weights["ram"]),
        ]
    )
    hybrid = Hybrid(index=index, era=era, source="geekbench")
    return with_relative(hybrid, CATEGORY, "perf", stats, cfg.tiers)


def _camera(cameras: list[dict[str, Any]], scales: dict[str, ReferenceScale]) -> float | None:
    if not cameras:
        return None
    main = next((c for c in cameras if c.get("type") == "main"), cameras[0])
    mp = main.get("mp")
    if mp is None:
        return None
    base = capability(float(mp), scales["main_camera_mp"])
    if base is None:
        return None
    ois_bonus = 8.0 if main.get("ois") else 0.0
    rear = [c for c in cameras if c.get("type") != "selfie"]
    versatility = min(len(rear), 4) / 4 * 20  # up to +20 for a full rear array
    return round(min(100.0, base * 0.6 + versatility + ois_bonus), 1)


def _battery(
    battery_mah: int,
    wired_w: float | None,
    wireless_w: float | None,
    process_nm: float | None,
    scales: dict[str, ReferenceScale],
) -> float | None:
    if battery_mah <= 0:
        return None
    capacity = capability(float(battery_mah), scales["battery_mah"])
    if capacity is None:
        return None
    wired = _coalesce(capability(wired_w, scales["charging_wired_w"]), 0.0) if wired_w else 0.0
    wireless = (
        _coalesce(capability(wireless_w, scales["charging_wireless_w"]), 0.0)
        if wireless_w
        else 0.0
    )
    efficiency = (
        _coalesce(capability(process_nm, scales["process_nm"]), 50.0)
        if process_nm is not None
        else 50.0
    )
    return round(capacity * 0.45 + wired * 0.20 + wireless * 0.10 + efficiency * 0.25, 1)


def _display(display: dict[str, Any], scales: dict[str, ReferenceScale]) -> float | None:
    if not display:
        return None
    refresh = display.get("refresh_hz")
    brightness = display.get("brightness_nits")
    ppi = display.get("ppi")
    if refresh is None and brightness is None and ppi is None:
        return None
    refresh_n = _coalesce(capability(refresh, scales["refresh_hz"]), 50.0)
    brightness_n = _coalesce(capability(brightness, scales["brightness_nits"]), 50.0)
    ppi_n = _coalesce(capability(ppi, scales["ppi"]), 50.0)
    return round(refresh_n * 0.35 + brightness_n * 0.35 + ppi_n * 0.30, 1)


def _value(
    overall: float | None, msrp_usd: int | None, scales: dict[str, ReferenceScale]
) -> float | None:
    if overall is None or not msrp_usd:
        return None
    affordability = capability(float(msrp_usd), scales["msrp_usd"])
    if affordability is None:
        return None
    return round(overall * 0.5 + affordability * 0.5, 1)


def score_phone(
    phone: Smartphone,
    soc: SoC,
    stats: StatsLike | None = None,
    config: ScoringConfig | None = None,
) -> PhoneScore:
    cfg = config or load_config()
    scales = cfg.reference_scales[CATEGORY]
    era = era_band(phone.release_date, cfg.era_bands)
    perf = _perf(phone, soc, cfg, era, stats)
    camera = _camera(phone.cameras, scales)
    battery = _battery(
        phone.battery_mah,
        phone.charging_wired_w,
        phone.charging_wireless_w,
        soc.process_nm,
        scales,
    )
    display = _display(phone.display, scales)
    components = [s for s in (perf.index, camera, battery, display) if s is not None]
    overall = round(sum(components) / len(components), 1) if components else None
    value = _value(overall, phone.msrp_usd, scales)
    return PhoneScore(
        algorithm_version=settings.scoring_algorithm_version,
        overall=overall,
        performance=perf.index,
        camera=camera,
        battery=battery,
        display=display,
        value=value,
        perf=perf,
    )
