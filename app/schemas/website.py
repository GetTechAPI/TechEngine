"""Website response schema (§6.12). Websites are unscored (no ``score`` field)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class WebsiteRead(BaseModel):
    """Full website detail response."""

    id: int
    slug: str
    name: str
    homepage_url: str | None = None
    launch_date: date | None = None
    owners: list[str]
    languages: list[str]
    verified: bool
    source_urls: list[str]
    created_at: datetime
    updated_at: datetime
    url: str  # API self-link, as on every other collection
