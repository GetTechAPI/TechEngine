"""CPU socket response schema (§6.8). Sockets are unscored (no ``score`` field)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import ManufacturerRef


class SocketRead(BaseModel):
    """Full socket detail response."""

    id: int
    slug: str
    name: str
    manufacturer: ManufacturerRef | None = None
    aliases: list[str]
    release_date: str | None = None
    package_type: str | None = None
    pin_count: int | None = None
    verified: bool
    source_urls: list[str]
    created_at: datetime
    updated_at: datetime
    url: str
