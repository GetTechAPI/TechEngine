"""Dataset cohorts for the within-generation relative view (§8, ADR-013).

The absolute index is self-contained (pinned scales); the relative percentile/tier
needs the whole category. ``DatasetStats`` bins every entity's absolute index by
(category, dimension, era band) once; ``percentile`` then bisects. Built once per
process (the served DB is static between dumps) and cached.
"""

from __future__ import annotations

from bisect import bisect_right

from sqlmodel import Session, select

from app.models.cpu import CPU
from app.models.gpu import DiscreteGPU
from app.models.smartphone import Smartphone
from app.models.soc import SoC
from app.services.scoring.common import Hybrid
from app.services.scoring.cpu import score_cpu
from app.services.scoring.gpu import score_gpu
from app.services.scoring.phones import score_phone
from app.services.scoring.soc import score_soc

CohortKey = tuple[str, str, str]


class DatasetStats:
    """Sorted absolute-index cohorts keyed by (category, dimension, era)."""

    def __init__(self, cohorts: dict[CohortKey, list[float]]) -> None:
        self._cohorts: dict[CohortKey, list[float]] = {
            key: sorted(values) for key, values in cohorts.items()
        }

    def percentile(
        self, category: str, dimension: str, era: str, index: float
    ) -> float | None:
        arr = self._cohorts.get((category, dimension, era))
        if not arr:
            return None
        return round(100.0 * bisect_right(arr, index) / len(arr), 1)

    def cohort_size(self, category: str, dimension: str, era: str) -> int:
        return len(self._cohorts.get((category, dimension, era), []))

    @classmethod
    def build(cls, session: Session) -> DatasetStats:
        cohorts: dict[CohortKey, list[float]] = {}

        def add(category: str, dimension: str, hybrid: Hybrid) -> None:
            if hybrid.index is None or hybrid.era is None:
                return
            cohorts.setdefault((category, dimension, hybrid.era), []).append(hybrid.index)

        for cpu in session.exec(select(CPU)).all():
            cpu_score = score_cpu(cpu)
            add("cpu", "single", cpu_score.single)
            add("cpu", "multi", cpu_score.multi)
        for gpu in session.exec(select(DiscreteGPU)).all():
            add("gpu", "graphics", score_gpu(gpu).graphics)
        socs: dict[int | None, SoC] = {}
        for soc in session.exec(select(SoC)).all():
            socs[soc.id] = soc
            soc_score = score_soc(soc)
            add("soc", "cpu", soc_score.cpu)
            add("soc", "system", soc_score.system)
        for phone in session.exec(select(Smartphone)).all():
            phone_soc = socs.get(phone.soc_id)
            if phone_soc is not None:
                add("phone", "perf", score_phone(phone, phone_soc).perf)

        return cls(cohorts)


_CACHE: DatasetStats | None = None


def get_dataset_stats(session: Session) -> DatasetStats:
    """Process-singleton cohorts (the served DB is static between dumps)."""
    global _CACHE
    if _CACHE is None:
        _CACHE = DatasetStats.build(session)
    return _CACHE


def clear_dataset_stats_cache() -> None:
    """Drop the cached cohorts (used by tests that swap the database)."""
    global _CACHE
    _CACHE = None
