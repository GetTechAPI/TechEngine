"""Game model (§6.10).

A video game. Unlike the hardware categories, a game references no Brand — its
makers are recorded as free-text ``developers`` / ``publishers`` lists (a game's
"brand" is its studio/publisher, which does not map onto the hardware Brand
catalogue). Games are unscored.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Game(SQLModel, table=True):
    """A video game title (e.g. The Witcher 3: Wild Hunt)."""

    __tablename__ = "games"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str

    release_date: date | None = None  # None for TBA/unreleased titles

    # Ratings / reception
    rating: float | None = None  # 0-5 aggregate user rating
    rating_count: int | None = None
    metacritic: int | None = None  # 0-100
    playtime_hours: int | None = None

    # Classification — stored as JSON string lists
    platforms: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    genres: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    stores: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    developers: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    publishers: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    esrb_rating: str | None = None

    background_image: str | None = None

    # Meta
    verified: bool = False
    source_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
