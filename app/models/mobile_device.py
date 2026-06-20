"""Mobile device models for tablets, watches, and PDAs."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MobileDeviceFields(SQLModel):
    """Shared columns for non-phone mobile device categories."""

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    base_model_slug: str | None = Field(default=None, index=True)
    name: str
    brand_id: int = Field(foreign_key="brands.id", index=True)
    soc_id: int | None = Field(default=None, foreign_key="socs.id", index=True)

    release_date: date
    msrp_usd: int | None = None

    ram_gb: float
    storage_options_gb: list[int] = Field(default_factory=list, sa_type=JSON)
    variant: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)

    display: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    cameras: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSON)

    battery_mah: int
    charging_wired_w: float | None = None
    charging_wireless_w: float | None = None

    weight_g: float
    dimensions: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    ip_rating: str | None = None

    os: str
    os_version: str | None = None
    connectivity: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)

    image_url: str | None = None
    images: list[str] = Field(default_factory=list, sa_type=JSON)

    verified: bool = False
    source_urls: list[str] = Field(default_factory=list, sa_type=JSON)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Tablet(MobileDeviceFields, table=True):
    """A tablet device variant."""

    __tablename__ = "tablets"


class Watch(MobileDeviceFields, table=True):
    """A smartwatch or connected wearable variant."""

    __tablename__ = "watches"


class PDA(MobileDeviceFields, table=True):
    """A PDA or handheld mobile computing variant."""

    __tablename__ = "pdas"
