"""Discrete GPU response schema (§6.5).

Proprietary benchmark numbers (e.g. 3DMark Time Spy) are not exposed; only the
open-licensed Blender Open Data score is surfaced (ADR-006, §8.5).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.schemas.common import HybridRead, ManufacturerRef


class GPUScoreRead(BaseModel):
    """Computed GPU scores (§8): a single graphics compute axis."""

    algorithm_version: str
    overall: float | None = None
    graphics: HybridRead


class GPURead(BaseModel):
    """Full discrete GPU detail response."""

    id: int
    slug: str
    name: str
    manufacturer: ManufacturerRef
    architecture: str | None = None
    release_date: date | None = None
    msrp_usd: int | None = None
    cuda_cores: int | None = None
    stream_processors: int | None = None
    rt_cores: int | None = None
    tensor_cores: int | None = None
    memory_gb: float | None = None
    memory_type: str | None = None
    memory_bus_bit: int | None = None
    memory_bandwidth_gbps: float | None = None
    base_clock_mhz: int | None = None
    boost_clock_mhz: int | None = None
    tdp_w: int | float | None = None
    pcie_version: str | None = None
    fp32_tflops: float | None = None
    blender_score: float | None = None
    score: GPUScoreRead
    verified: bool
    source_urls: list[str]
    url: str
