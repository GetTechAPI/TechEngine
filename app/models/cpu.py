"""Computer CPU model — desktop/laptop processors (Intel Core, AMD Ryzen).

Not part of the original v1 schema (§6); added per maintainer request and recorded
in docs/SPEC.md (§6.7 / ADR-011). Distinct from ``SoC`` (mobile) and ``DiscreteGPU``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CPU(SQLModel, table=True):
    """A desktop or laptop CPU (e.g. Core i9-14900K, Ryzen 9 7950X)."""

    __tablename__ = "cpus"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    manufacturer_id: int = Field(foreign_key="brands.id", index=True)
    release_date: date
    segment: str  # "desktop" | "laptop" | "hedt" | "server"
    architecture: str  # "Raptor Lake", "Zen 4"
    socket: str | None = None  # "LGA1700", "AM5"
    process_node: str | None = None  # "Intel 7", "TSMC N4" (CPU nodes resist a single nm float)

    # Cores
    cores: int
    threads: int
    p_cores: int | None = None
    e_cores: int | None = None

    # Clocks
    base_clock_ghz: float | None = None
    boost_clock_ghz: float | None = None

    # Cache / power
    l3_cache_mb: float | None = None
    tdp_w: int | None = None  # base power (PBP/TDP)
    max_tdp_w: int | None = None  # turbo power (MTP/PPT)

    # Platform
    integrated_graphics: str | None = None
    memory_support: str | None = None  # "DDR5-5600"

    # Benchmarks (raw, algorithm input only — ADR-006)
    # Modern (current generation)
    cinebench_r23_single: int | None = None
    cinebench_r23_multi: int | None = None
    # Cinebench 2024 — Maxon's current release (superseded R23, Redshift engine);
    # much smaller scale (single ~100-140, multi ~hundreds-thousands).
    cinebench_2024_single: int | None = None
    cinebench_2024_multi: int | None = None
    geekbench_single: int | None = None
    geekbench_multi: int | None = None
    # Legacy benchmark programs — added per maintainer request to score pre-R23 CPUs.
    # Cinebench R15/R10 are integer scores; R11.5 reports small decimals (e.g. 1.52).
    cinebench_r15_single: int | None = None
    cinebench_r15_multi: int | None = None
    cinebench_r11_5_single: float | None = None
    cinebench_r11_5_multi: float | None = None
    cinebench_r10_single: int | None = None
    cinebench_r10_multi: int | None = None
    # PassMark CPU Mark — single-thread rating + overall mark.
    passmark_single: int | None = None
    passmark_cpu_mark: int | None = None
    # SPEC CPU2006 base rates (workstation/server era).
    specint2006: float | None = None
    specfp2006: float | None = None
    # Classic synthetics for 1990s–2000s parts.
    dhrystone_mips: float | None = None
    whetstone_mflops: float | None = None
    superpi_1m_sec: float | None = None  # SuperPI 1M time in seconds (lower is better)

    # Meta
    msrp_usd: int | None = None
    verified: bool = False
    source_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
