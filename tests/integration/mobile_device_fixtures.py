"""Small database fixtures for mobile-device endpoint tests."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from app.database import engine
from app.models.brand import Brand
from app.models.mobile_device import PDA, Tablet, Watch


def _brand_id(session: Session, slug: str, name: str, country: str) -> int:
    brand = session.exec(select(Brand).where(Brand.slug == slug)).first()
    if brand is None:
        brand = Brand(slug=slug, name=name, country=country, source_urls=["https://example.com"])
        session.add(brand)
        session.commit()
        session.refresh(brand)
    assert brand.id is not None
    return brand.id


def ensure_mobile_device_fixtures() -> None:
    """Insert compact tablet, watch, and PDA records when the data checkout lacks them."""

    with Session(engine) as session:
        apple_id = _brand_id(session, "apple", "Apple", "US")
        samsung_id = _brand_id(session, "samsung", "Samsung", "KR")
        hp_id = _brand_id(session, "hp", "HP", "US")

        tablet = session.exec(
            select(Tablet).where(Tablet.slug == "ipad-pro-11-m4-wifi-8gb-256gb")
        ).first()
        if tablet is None:
            session.add(
                Tablet(
                    slug="ipad-pro-11-m4-wifi-8gb-256gb",
                    base_model_slug="ipad-pro-11-m4",
                    name="Apple iPad Pro 11-inch (M4, Wi-Fi, 256GB)",
                    brand_id=apple_id,
                    release_date=date(2024, 5, 15),
                    msrp_usd=999,
                    ram_gb=8,
                    storage_options_gb=[256],
                    variant={
                        "region": "global",
                        "memory": {"ram_gb": 8, "storage_gb": 256},
                        "network": {"cellular": "Wi-Fi", "carrier": "none"},
                    },
                    display={"size_inch": 11.0, "refresh_hz": 120},
                    cameras=[{"type": "wide", "mp": 12}],
                    battery_mah=8160,
                    weight_g=444,
                    os="iPadOS",
                    verified=False,
                    source_urls=["https://support.apple.com/"],
                )
            )

        watch = session.exec(
            select(Watch).where(Watch.slug == "galaxy-watch-global-bluetooth-42mm")
        ).first()
        if watch is None:
            session.add(
                Watch(
                    slug="galaxy-watch-global-bluetooth-42mm",
                    base_model_slug="galaxy-watch",
                    name="Samsung Galaxy Watch 42mm Bluetooth",
                    brand_id=samsung_id,
                    release_date=date(2018, 8, 24),
                    msrp_usd=329,
                    ram_gb=0.75,
                    storage_options_gb=[4],
                    variant={"region": "global", "network": {"cellular": "none"}},
                    display={"size_inch": 1.2},
                    cameras=[],
                    battery_mah=270,
                    weight_g=49,
                    os="Tizen",
                    verified=False,
                    source_urls=["https://www.samsung.com/"],
                )
            )

        if session.exec(select(PDA).where(PDA.slug == "ipaq-h3600-base")).first() is None:
            session.add(
                PDA(
                    slug="ipaq-h3600-base",
                    base_model_slug="ipaq-h3600",
                    name="HP iPAQ H3600",
                    brand_id=hp_id,
                    release_date=date(2000, 4, 1),
                    ram_gb=0.032,
                    storage_options_gb=[],
                    variant={"region": "global"},
                    display={"size_inch": 3.8},
                    cameras=[],
                    battery_mah=1500,
                    weight_g=178,
                    os="Pocket PC",
                    verified=False,
                    source_urls=["https://en.wikipedia.org/wiki/IPAQ"],
                )
            )

        session.commit()
