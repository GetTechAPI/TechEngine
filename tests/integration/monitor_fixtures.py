"""Small database fixtures for monitor endpoint tests."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from app.database import engine
from app.models.brand import Brand
from app.models.monitor import Monitor


def _brand_id(session: Session, slug: str, name: str, country: str) -> int:
    brand = session.exec(select(Brand).where(Brand.slug == slug)).first()
    if brand is None:
        brand = Brand(slug=slug, name=name, country=country, source_urls=["https://example.com"])
        session.add(brand)
        session.commit()
        session.refresh(brand)
    assert brand.id is not None
    return brand.id


def ensure_monitor_fixtures() -> None:
    """Insert a compact monitor (plus its brand) when the data checkout lacks it."""

    with Session(engine) as session:
        lg_id = _brand_id(session, "lg", "LG", "KR")

        monitor = session.exec(
            select(Monitor).where(Monitor.slug == "lg-ultragear-27gp850-test")
        ).first()
        if monitor is None:
            session.add(
                Monitor(
                    slug="lg-ultragear-27gp850-test",
                    base_model_slug="lg-ultragear-27gp850",
                    name="LG UltraGear 27GP850 (test)",
                    brand_id=lg_id,
                    release_date=date(2023, 1, 1),
                    msrp_usd=399,
                    size_inch=27.0,
                    resolution="2560x1440",
                    aspect_ratio="16:9",
                    refresh_hz=165,
                    panel_type="IPS",
                    hdr="HDR10",
                    features={"ports": ["HDMI 2.0", "DisplayPort 1.4"], "adaptive_sync": "G-Sync"},
                    rating=4.6,
                    source_urls=["https://example.com"],
                )
            )
            session.commit()
