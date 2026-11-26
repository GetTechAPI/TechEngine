"""CPU socket model (§6.8).

The physical interface a CPU plugs into. Unscored.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Socket(SQLModel, table=True):
    """A CPU socket (e.g. LGA 1700, AM5)."""

    __tablename__ = "sockets"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    # None = a socket no single maker owns (e.g. Socket 7).
    manufacturer_id: int | None = Field(default=None, foreign_key="brands.id", index=True)
    aliases: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # "YYYY", "YYYY-MM" or "YYYY-MM-DD": kept as written, sources often give the year only.
    release_date: str | None = None
    package_type: str | None = None  # "LGA", "PGA"
    pin_count: int | None = None

    # Meta
    verified: bool = False
    source_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
