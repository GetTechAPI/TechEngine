"""Laptop model (§6.8).

A laptop references a Brand (required) and, when resolvable, a CPU and/or a
discrete GPU by foreign key. The raw ``cpu_name`` / ``gpu_name`` strings are
always kept so a record is meaningful even when the component is not (yet) in
the catalog. Laptops are currently unscored.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Laptop(SQLModel, table=True):
    """A laptop model/variant (e.g. Lenovo Legion Pro 5 16IAX10)."""

    __tablename__ = "laptops"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    base_model_slug: str | None = Field(default=None, index=True)
    name: str
    brand_id: int = Field(foreign_key="brands.id", index=True)
    cpu_id: int | None = Field(default=None, foreign_key="cpus.id", index=True)
    gpu_id: int | None = Field(default=None, foreign_key="gpus.id", index=True)

    release_date: date
    msrp_usd: int | None = None
    device_category: str | None = None  # e.g. "Gaming", "Thin & Light", "2-in-1"

    # Component descriptors (raw, always present even when the FK is unresolved)
    cpu_name: str | None = None
    gpu_name: str | None = None
    gpu_type: str | None = None  # "Dedicated" | "Integrated"

    # Memory / storage
    ram_gb: int
    storage_gb: int | None = None

    # Display — {size_inch, resolution, refresh_hz, panel, ...}
    display: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # Physical
    weight_g: float | None = None

    # Software
    os: str
    os_version: str | None = None

    # Source tracking (dataset row provenance)
    variant: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # Assets
    image_url: str | None = None

    # Meta
    verified: bool = False
    source_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
