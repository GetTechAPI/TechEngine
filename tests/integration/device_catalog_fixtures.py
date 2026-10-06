"""Small database fixtures for device catalog endpoint tests."""

from __future__ import annotations

from sqlmodel import Session, select

from app.database import engine
from app.models.brand import Brand
from app.models.device_catalog import DeviceCatalog


def ensure_device_catalog_fixtures() -> None:
    """Insert a brand without a country plus one catalog entry, when missing."""

    with Session(engine) as session:
        brand = session.exec(select(Brand).where(Brand.slug == "catalog-test-oem")).first()
        if brand is None:
            brand = Brand(
                slug="catalog-test-oem",
                name="Catalog Test OEM",
                country=None,
                source_urls=["https://example.com"],
            )
            session.add(brand)
            session.commit()
            session.refresh(brand)
        assert brand.id is not None

        entry = session.exec(
            select(DeviceCatalog).where(DeviceCatalog.slug == "catalog-test-phone-x1")
        ).first()
        if entry is None:
            session.add(
                DeviceCatalog(
                    slug="catalog-test-phone-x1",
                    base_model_slug="catalog-test-phone-x1-base",
                    name="Catalog Test Phone X1",
                    brand_id=brand.id,
                    model_numbers=["CT-X1A", "CT-X1B"],
                    codenames=["ctx1"],
                    marketing_names=["Phone X1"],
                    form_factor="phone",
                    android_sdk_min=30,
                    source_urls=["https://example.com/supported_devices.csv"],
                )
            )
            session.commit()
