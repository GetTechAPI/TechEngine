"""Response schemas for tablets, watches, and PDAs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.brand import BrandSummary
from app.schemas.soc import SoCSummary


class MobileDeviceRead(BaseModel):
    """Full mobile device detail response."""

    id: int
    slug: str
    base_model_slug: str | None = None
    name: str
    brand: BrandSummary
    soc: SoCSummary | None = None
    release_date: date
    msrp_usd: int | None = None
    ram_gb: float
    storage_options_gb: list[int]
    variant: dict[str, Any]
    display: dict[str, Any]
    cameras: list[dict[str, Any]]
    battery_mah: int
    charging_wired_w: float | None = None
    charging_wireless_w: float | None = None
    weight_g: float
    dimensions: dict[str, Any]
    ip_rating: str | None = None
    os: str
    os_version: str | None = None
    connectivity: dict[str, Any]
    image_url: str | None = None
    images: list[str] = Field(default_factory=list)
    verified: bool
    source_urls: list[str]
    created_at: datetime
    updated_at: datetime
    url: str
