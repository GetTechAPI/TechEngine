"""Software response schema (§6.11). Software is unscored (no ``score`` field)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class SoftwareRead(BaseModel):
    """Full software detail response."""

    id: int
    slug: str
    name: str
    release_date: date | None = None
    developers: list[str]
    publishers: list[str]
    operating_systems: list[str]
    programming_languages: list[str]
    licenses: list[str]
    genres: list[str]
    verified: bool
    source_urls: list[str]
    created_at: datetime
    updated_at: datetime
    url: str
