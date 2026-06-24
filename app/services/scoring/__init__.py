"""Open scoring algorithm (§8, ADR-013) — hybrid absolute(log) + within-era relative.

Scores are 0-100, carry ``algorithm_version``, and are ``None`` when their benchmark
inputs are missing (never 0). Per category: ``score_phone/score_cpu/score_gpu/score_soc``.
Pass a ``DatasetStats`` (``get_dataset_stats(session)``) to fill the relative
percentile/tier; without it only the self-contained absolute index is returned.
"""

from __future__ import annotations

from app.config import settings
from app.services.scoring.common import Hybrid, ReferenceScale, capability
from app.services.scoring.config import ScoringConfig, load_config
from app.services.scoring.cpu import CPUScore, score_cpu
from app.services.scoring.gpu import GPUScore, score_gpu
from app.services.scoring.phones import PhoneScore, score_phone
from app.services.scoring.soc import SoCScore, score_soc
from app.services.scoring.stats import (
    DatasetStats,
    clear_dataset_stats_cache,
    get_dataset_stats,
)

ALGORITHM_VERSION = settings.scoring_algorithm_version

__all__ = [
    "ALGORITHM_VERSION",
    "CPUScore",
    "DatasetStats",
    "GPUScore",
    "Hybrid",
    "PhoneScore",
    "ReferenceScale",
    "ScoringConfig",
    "SoCScore",
    "capability",
    "clear_dataset_stats_cache",
    "get_dataset_stats",
    "load_config",
    "score_cpu",
    "score_gpu",
    "score_phone",
    "score_soc",
]
