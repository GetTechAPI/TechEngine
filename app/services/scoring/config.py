"""Load and validate ``config/scoring.yaml`` into typed structures (§8.2).

Reference scales are pinned and versioned: the loaded ``algorithm_version`` must
equal ``settings.scoring_algorithm_version`` (fail fast on drift). Cached per path.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import settings
from app.services.scoring.common import ReferenceScale


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    algorithm_version: str
    reference_scales: dict[str, dict[str, ReferenceScale]]
    chains: dict[str, dict[str, list[str]]]
    weights: dict[str, dict[str, float]]
    era_bands: list[tuple[int, int, str]]
    tiers: list[tuple[float, str]]

    def scale(self, category: str, benchmark: str) -> ReferenceScale | None:
        return self.reference_scales.get(category, {}).get(benchmark)


def _as_scale(raw: dict[str, Any]) -> ReferenceScale:
    return ReferenceScale(
        lo=float(raw["lo"]),
        hi=float(raw["hi"]),
        log=bool(raw.get("log", False)),
        invert=bool(raw.get("invert", False)),
    )


def _parse(raw: dict[str, Any]) -> ScoringConfig:
    scales: dict[str, dict[str, ReferenceScale]] = {
        cat: {name: _as_scale(spec) for name, spec in benches.items()}
        for cat, benches in raw["reference_scales"].items()
    }
    chains: dict[str, dict[str, list[str]]] = {
        cat: {dim: [str(b) for b in benches] for dim, benches in dims.items()}
        for cat, dims in raw["chains"].items()
    }
    weights: dict[str, dict[str, float]] = {
        key: {k: float(v) for k, v in group.items()} for key, group in raw["weights"].items()
    }
    era_bands: list[tuple[int, int, str]] = [
        (int(lo), int(hi), str(label)) for lo, hi, label in raw["era_bands"]
    ]
    tiers: list[tuple[float, str]] = [
        (float(threshold), str(label)) for threshold, label in raw["tiers"]
    ]
    return ScoringConfig(
        algorithm_version=str(raw["algorithm_version"]),
        reference_scales=scales,
        chains=chains,
        weights=weights,
        era_bands=era_bands,
        tiers=tiers,
    )


@lru_cache(maxsize=4)
def load_config(path: str | None = None) -> ScoringConfig:
    """Parse the scoring config; assert its version matches settings."""
    cfg_path = Path(path or settings.scoring_config_path)
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"scoring config at {cfg_path} is not a mapping")
    config = _parse(raw)
    if config.algorithm_version != settings.scoring_algorithm_version:
        raise ValueError(
            f"scoring.yaml version {config.algorithm_version!r} != "
            f"settings {settings.scoring_algorithm_version!r}"
        )
    return config
