"""Device catalog response schema. Identity-only and unscored (no ``score`` field)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.brand import BrandSummary
from app.schemas.soc import SoCSummary


class DeviceCatalogRead(BaseModel):
    """Full device catalog detail response."""

    id: int
    slug: str
    base_model_slug: str | None = None
    name: str
    brand: BrandSummary
    soc: SoCSummary | None = None
    model_numbers: list[str]
    codenames: list[str]
    marketing_names: list[str]
    form_factor: str | None = None
    device_type_guess: str | None = None
    ram_gb: float | None = None
    soc_raw: str | None = None
    gpu_raw: str | None = None
    screen_resolution: str | None = None
    screen_density_dpi: int | None = None
    android_sdk_min: int | None = None
    android_sdk_max: int | None = None
    release_year: int | None = None
    release_year_source: str | None = None
    promoted_to: str | None = None
    verified: bool
    source_urls: list[str]
    created_at: datetime
    updated_at: datetime
    url: str
