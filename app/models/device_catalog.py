"""Device catalog model: identity-only device records.

A catalog entry anchors a device that is known to exist (brand, marketing name,
model numbers, codenames — e.g. from Google Play's supported device list) but
has no spec sheet yet. Nothing here is estimated: unknown fields stay null.
Once a full record exists, ``promoted_to`` points at it. Unscored.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DeviceCatalog(SQLModel, table=True):
    """An identity-only device (e.g. a Play-listed phone with no spec source yet)."""

    __tablename__ = "device_catalog"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    base_model_slug: str | None = Field(default=None, index=True)
    name: str
    brand_id: int = Field(foreign_key="brands.id", index=True)
    soc_id: int | None = Field(default=None, foreign_key="socs.id", index=True)

    model_numbers: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    codenames: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    marketing_names: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # phone | tablet | watch | tv | other — only when a source states it.
    form_factor: str | None = None
    # Same vocabulary, but a heuristic guess (name/model patterns), never a fact.
    device_type_guess: str | None = None
    # Play Console device-catalog specs, verbatim where they are strings.
    ram_gb: float | None = None
    soc_raw: str | None = None
    gpu_raw: str | None = None
    screen_resolution: str | None = None  # "<w>x<h>", e.g. "1080x2400"
    screen_density_dpi: int | None = None
    android_sdk_min: int | None = None  # an upper bound on launch year, not a date
    android_sdk_max: int | None = None
    # Only with provenance: "model_code" (vendor code encodes the year) | "record".
    release_year: int | None = None
    release_year_source: str | None = None
    # "<category>/<slug>" of the full record once one exists.
    promoted_to: str | None = None

    # Meta
    verified: bool = False
    source_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
