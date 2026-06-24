"""Maintainer CLI: suggest pinned reference scales (p01..p99) from the seeded DB.

Run: ``python -m app.services.scoring.calibrate``. Prints YAML-ready ``{lo, hi}`` per
benchmark so a maintainer can paste calibrated bounds into ``config/scoring.yaml`` and
bump ``scoring_algorithm_version``. Writes nothing — keeps the pinned+versioned
guarantee. Not run in CI (coverage-omitted).
"""

from __future__ import annotations

from typing import Any

CPU_BENCHES = [
    "cinebench_r23_single", "cinebench_r23_multi", "geekbench_single", "geekbench_multi",
    "passmark_single", "passmark_cpu_mark", "cinebench_2024_single", "cinebench_2024_multi",
    "cinebench_r15_single", "cinebench_r15_multi", "cinebench_r11_5_single",
    "cinebench_r11_5_multi", "cinebench_r10_single", "cinebench_r10_multi",
    "specint2006", "specfp2006", "dhrystone_mips", "whetstone_mflops", "superpi_1m_sec",
]
GPU_BENCHES = [
    "timespy_score", "timespy_extreme_score", "passmark_g3d_mark", "fp32_tflops",
    "blender_score", "speedway_score", "octanebench_score",
]
SOC_BENCHES = ["geekbench_single", "geekbench_multi", "antutu_score"]


def _percentiles(values: list[float]) -> tuple[float, float] | None:
    vals = sorted(values)
    if len(vals) < 5:
        return None

    def at(p: float) -> float:
        idx = min(len(vals) - 1, max(0, round(p / 100 * (len(vals) - 1))))
        return vals[idx]

    return at(1), at(99)


def main() -> None:
    from sqlmodel import Session, select

    from app.database import create_db_and_tables, engine
    from app.models.cpu import CPU
    from app.models.gpu import DiscreteGPU
    from app.models.soc import SoC
    from app.seed import seed

    create_db_and_tables()
    print("# suggested reference_scales (p01..p99) — paste into config/scoring.yaml")
    with Session(engine) as session:
        seed(session)
        groups: list[tuple[str, type, list[str]]] = [
            ("cpu", CPU, CPU_BENCHES),
            ("gpu", DiscreteGPU, GPU_BENCHES),
            ("soc", SoC, SOC_BENCHES),
        ]
        for label, model, benches in groups:
            print(f"  {label}:")
            rows: list[Any] = list(session.exec(select(model)).all())
            for bench in benches:
                vals = [
                    float(getattr(row, bench))
                    for row in rows
                    if getattr(row, bench, None) is not None
                ]
                bounds = _percentiles(vals)
                if bounds is None:
                    print(f"    # {bench}: insufficient data (n={len(vals)})")
                else:
                    lo, hi = bounds
                    print(f"    {bench}: {{ lo: {lo:g}, hi: {hi:g}, log: true }}  # n={len(vals)}")


if __name__ == "__main__":
    main()
