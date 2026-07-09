"""Game response schema (§6.10). Games are unscored (no ``score`` field)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class GameRead(BaseModel):
    """Full game detail response."""

    id: int
    slug: str
    name: str
    release_date: date | None = None
    rating: float | None = None
    rating_count: int | None = None
    metacritic: int | None = None
    playtime_hours: int | None = None
    platforms: list[str]
    genres: list[str]
    stores: list[str]
    developers: list[str]
    publishers: list[str]
    tags: list[str]
    esrb_rating: str | None = None
    background_image: str | None = None
    verified: bool
    source_urls: list[str]
    created_at: datetime
    updated_at: datetime
    url: str
