"""Monitor model (§6.9).

A computer monitor references a Brand (required). Display specs (size,
resolution, refresh, panel, curvature, HDR) are first-class fields. Monitors
are currently unscored.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Monitor(SQLModel, table=True):
    """A computer monitor model/variant (e.g. LG UltraGear 27GP850)."""

    __tablename__ = "monitors"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    base_model_slug: str | None = Field(default=None, index=True)
    name: str
    brand_id: int = Field(foreign_key="brands.id", index=True)

    release_date: date
    msrp_usd: int | None = None

    # Display
    size_inch: float
    resolution: str  # e.g. "1920x1080"
    aspect_ratio: str | None = None  # e.g. "16:9"
    refresh_hz: int | None = None
    panel_type: str | None = None  # IPS / VA / OLED / TN
    curvature: str | None = None  # e.g. "1500R"
    hdr: str | None = None
    ppi: int | None = None

    # Extras — {ports: [...], speakers, adaptive_sync, ...}
    features: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    rating: float | None = None

    # Source tracking
    variant: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    image_url: str | None = None

    # Meta
    verified: bool = False
    source_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
