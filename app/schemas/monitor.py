"""Monitor response schema (§6.9). Monitors are unscored (no ``score`` field)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.brand import BrandSummary


class MonitorRead(BaseModel):
    """Full monitor detail response."""

    id: int
    slug: str
    base_model_slug: str | None = None
    name: str
    brand: BrandSummary
    release_date: date
    msrp_usd: int | None = None
    size_inch: float
    resolution: str
    aspect_ratio: str | None = None
    refresh_hz: int | None = None
    panel_type: str | None = None
    curvature: str | None = None
    hdr: str | None = None
    ppi: int | None = None
    features: dict[str, Any]
    rating: float | None = None
    variant: dict[str, Any]
    image_url: str | None = None
    verified: bool
    source_urls: list[str]
    created_at: datetime
    updated_at: datetime
    url: str
