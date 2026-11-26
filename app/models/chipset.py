"""Motherboard chipset model (§6.8).

References its maker (Brand) and, by slug, the sockets it supports. Unscored.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Chipset(SQLModel, table=True):
    """A motherboard chipset (e.g. Intel Z790, AMD X670E)."""

    __tablename__ = "chipsets"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    manufacturer_id: int = Field(foreign_key="brands.id", index=True)
    aliases: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    series: str | None = None

    # "YYYY", "YYYY-MM" or "YYYY-MM-DD": kept as written, sources often give the year only.
    release_date: str | None = None
    # Socket slugs; an empty list = no source names a socket.
    sockets: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    segment: str | None = None  # "desktop" | "mobile" | "server" | "workstation" | "embedded"

    # Platform (None = unknown)
    cpu_overclocking: bool | None = None
    memory_types: list[str] | None = Field(default=None, sa_column=Column(JSON))
    max_memory_gb: float | None = None
    pcie_version: str | None = None
    usb_ports: dict[str, int] | None = Field(default=None, sa_column=Column(JSON))
    sata_ports: int | None = None
    tdp_w: float | None = None
    process_nm: float | None = None

    # Meta
    verified: bool = False
    source_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
