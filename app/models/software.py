"""Software model (§6.11).

A software application/program. Like games, software references no Brand — its
makers are free-text ``developers`` / ``publishers`` lists. Unscored.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Software(SQLModel, table=True):
    """A software product (e.g. Blender, Firefox)."""

    __tablename__ = "software"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str

    release_date: date | None = None

    developers: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    publishers: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    operating_systems: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    programming_languages: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    licenses: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    genres: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # Meta
    verified: bool = False
    source_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
