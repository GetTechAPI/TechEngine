"""GPU scoring (§8) — a single graphics compute axis, benchmark-only."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.models.gpu import DiscreteGPU
from app.services.scoring.common import Hybrid, StatsLike, axis, era_band, with_relative
from app.services.scoring.config import ScoringConfig, load_config

CATEGORY = "gpu"


@dataclass(slots=True)
class GPUScore:
    algorithm_version: str
    overall: float | None
    graphics: Hybrid


def score_gpu(
    gpu: DiscreteGPU, stats: StatsLike | None = None, config: ScoringConfig | None = None
) -> GPUScore:
    cfg = config or load_config()
    era = era_band(gpu.release_date, cfg.era_bands)
    scales = cfg.reference_scales[CATEGORY]
    chain = cfg.chains[CATEGORY]["graphics"]
    raw: dict[str, float | None] = {name: getattr(gpu, name, None) for name in chain}
    graphics = with_relative(
        axis(raw, chain, scales, era), CATEGORY, "graphics", stats, cfg.tiers
    )
    return GPUScore(
        algorithm_version=settings.scoring_algorithm_version,
        overall=graphics.index,
        graphics=graphics,
    )
