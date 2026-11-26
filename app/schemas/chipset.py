"""Chipset response schema (§6.8). Chipsets are unscored (no ``score`` field)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ManufacturerRef, ResourceRef


class ChipsetRead(BaseModel):
    """Full chipset detail response."""

    id: int
    slug: str
    name: str
    manufacturer: ManufacturerRef
    aliases: list[str]
    series: str | None = None
    release_date: str | None = None
    sockets: list[ResourceRef]
    segment: str | None = None
    cpu_overclocking: bool | None = None
    memory_types: list[str] | None = None
    max_memory_gb: float | None = None
    pcie_version: str | None = None
    usb_ports: dict[str, int] | None = None
    sata_ports: int | None = None
    tdp_w: float | None = None
    process_nm: float | None = None
    verified: bool
    source_urls: list[str]
    created_at: datetime
    updated_at: datetime
    url: str
