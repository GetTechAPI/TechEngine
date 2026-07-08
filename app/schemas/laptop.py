"""Laptop response schema (§6.8).

Laptops are unscored (no ``score`` field). CPU and GPU are embedded as
lightweight references when resolved to the catalog; otherwise only the raw
``cpu_name`` / ``gpu_name`` strings are present.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.brand import BrandSummary
from app.schemas.common import ResourceRef


class LaptopRead(BaseModel):
    """Full laptop detail response."""

    id: int
    slug: str
    base_model_slug: str | None = None
    name: str
    brand: BrandSummary
    cpu: ResourceRef | None = None
    gpu: ResourceRef | None = None
    release_date: date
    msrp_usd: int | None = None
    device_category: str | None = None
    cpu_name: str | None = None
    gpu_name: str | None = None
    gpu_type: str | None = None
    ram_gb: int
    storage_gb: int | None = None
    display: dict[str, Any]
    weight_g: float | None = None
    os: str
    os_version: str | None = None
    variant: dict[str, Any]
    image_url: str | None = None
    verified: bool
    source_urls: list[str]
    created_at: datetime
    updated_at: datetime
    url: str
