"""SoC scoring (§8) — a blended CPU axis (Geekbench) + a system axis (AnTuTu)."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.models.soc import SoC
from app.services.scoring.common import (
    Hybrid,
    StatsLike,
    axis,
    capability,
    combine,
    era_band,
    with_relative,
)
from app.services.scoring.config import ScoringConfig, load_config

CATEGORY = "soc"


@dataclass(slots=True)
class SoCScore:
    algorithm_version: str
    overall: float | None
    cpu: Hybrid
    system: Hybrid


def _cpu_axis(soc: SoC, cfg: ScoringConfig, era: str | None) -> Hybrid:
    scales = cfg.reference_scales[CATEGORY]
    weights = cfg.weights["soc_cpu"]
    single = capability(soc.geekbench_single, scales["geekbench_single"])
    multi = capability(soc.geekbench_multi, scales["geekbench_multi"])
    index = combine([(single, weights["single"]), (multi, weights["multi"])])
    return Hybrid(index=index, era=era, source="geekbench" if index is not None else None)


def score_soc(
    soc: SoC, stats: StatsLike | None = None, config: ScoringConfig | None = None
) -> SoCScore:
    cfg = config or load_config()
    era = era_band(soc.release_date, cfg.era_bands)
    scales = cfg.reference_scales[CATEGORY]
    chains = cfg.chains[CATEGORY]
    raw: dict[str, float | None] = {"antutu_score": soc.antutu_score}
    cpu = with_relative(_cpu_axis(soc, cfg, era), CATEGORY, "cpu", stats, cfg.tiers)
    system = with_relative(
        axis(raw, chains["system"], scales, era), CATEGORY, "system", stats, cfg.tiers
    )
    weights = cfg.weights["soc"]
    overall = combine([(cpu.index, weights["cpu"]), (system.index, weights["system"])])
    return SoCScore(
        algorithm_version=settings.scoring_algorithm_version,
        overall=overall,
        cpu=cpu,
        system=system,
    )
