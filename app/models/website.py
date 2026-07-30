"""Website model (§6.12).

A website or web service. Like games and software, a website references no
Brand — its operators are free-text ``owners``. Unscored.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Website(SQLModel, table=True):
    """A website (e.g. Wikipedia, Hacker News)."""

    __tablename__ = "website"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str

    # The site's own address. Named homepage_url because ``url`` is reserved
    # across every read schema for the API self-link.
    homepage_url: str | None = None
    launch_date: date | None = None

    owners: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    languages: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # Meta
    verified: bool = False
    source_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
