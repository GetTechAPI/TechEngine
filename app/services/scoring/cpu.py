"""CPU scoring (§8) — single/multi compute axes, benchmark-only."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.models.cpu import CPU
from app.services.scoring.common import (
    Hybrid,
    StatsLike,
    axis,
    combine,
    era_band,
    with_relative,
)
from app.services.scoring.config import ScoringConfig, load_config

CATEGORY = "cpu"


@dataclass(slots=True)
class CPUScore:
    algorithm_version: str
    overall: float | None
    single: Hybrid
    multi: Hybrid


def score_cpu(
    cpu: CPU, stats: StatsLike | None = None, config: ScoringConfig | None = None
) -> CPUScore:
    cfg = config or load_config()
    era = era_band(cpu.release_date, cfg.era_bands)
    scales = cfg.reference_scales[CATEGORY]
    chains = cfg.chains[CATEGORY]
    raw: dict[str, float | None] = {
        name: getattr(cpu, name, None) for dim in chains.values() for name in dim
    }
    single = with_relative(
        axis(raw, chains["single"], scales, era), CATEGORY, "single", stats, cfg.tiers
    )
    multi = with_relative(
        axis(raw, chains["multi"], scales, era), CATEGORY, "multi", stats, cfg.tiers
    )
    weights = cfg.weights["cpu"]
    overall = combine([(single.index, weights["single"]), (multi.index, weights["multi"])])
    return CPUScore(
        algorithm_version=settings.scoring_algorithm_version,
        overall=overall,
        single=single,
        multi=multi,
    )
