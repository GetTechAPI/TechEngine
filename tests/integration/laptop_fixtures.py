"""Small database fixtures for laptop endpoint tests."""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, select

from app.database import engine
from app.models.brand import Brand
from app.models.cpu import CPU
from app.models.gpu import DiscreteGPU
from app.models.laptop import Laptop


def _brand_id(session: Session, slug: str, name: str, country: str) -> int:
    brand = session.exec(select(Brand).where(Brand.slug == slug)).first()
    if brand is None:
        brand = Brand(slug=slug, name=name, country=country, source_urls=["https://example.com"])
        session.add(brand)
        session.commit()
        session.refresh(brand)
    assert brand.id is not None
    return brand.id


def ensure_laptop_fixtures() -> None:
    """Insert a compact laptop (plus its brand/CPU/GPU) when the data checkout lacks it."""

    with Session(engine) as session:
        lenovo_id = _brand_id(session, "lenovo", "Lenovo", "CN")
        intel_id = _brand_id(session, "intel", "Intel", "US")
        nvidia_id = _brand_id(session, "nvidia", "NVIDIA", "US")

        cpu = session.exec(select(CPU).where(CPU.slug == "test-core-ultra-7-255hx")).first()
        if cpu is None:
            cpu = CPU(
                slug="test-core-ultra-7-255hx",
                name="Intel Core Ultra 7 255HX",
                manufacturer_id=intel_id,
                release_date=date(2024, 1, 1),
                segment="laptop",
                architecture="Arrow Lake",
                cores=20,
                threads=20,
                source_urls=["https://example.com"],
            )
            session.add(cpu)
            session.commit()
            session.refresh(cpu)

        gpu = session.exec(
            select(DiscreteGPU).where(DiscreteGPU.slug == "test-rtx-5060-laptop")
        ).first()
        if gpu is None:
            gpu = DiscreteGPU(
                slug="test-rtx-5060-laptop",
                name="NVIDIA GeForce RTX 5060 Laptop",
                manufacturer_id=nvidia_id,
                architecture="Blackwell",
                release_date=date(2025, 1, 1),
                memory_gb=8.0,
                memory_type="GDDR7",
                memory_bus_bit=128,
                base_clock_mhz=1500,
                boost_clock_mhz=2200,
                tdp_w=115,
                pcie_version="5.0",
                source_urls=["https://example.com"],
            )
            session.add(gpu)
            session.commit()
            session.refresh(gpu)

        laptop = session.exec(
            select(Laptop).where(Laptop.slug == "lenovo-legion-pro-5-test")
        ).first()
        if laptop is None:
            session.add(
                Laptop(
                    slug="lenovo-legion-pro-5-test",
                    base_model_slug="legion-pro-5",
                    name="Lenovo Legion Pro 5 (test)",
                    brand_id=lenovo_id,
                    cpu_id=cpu.id,
                    gpu_id=gpu.id,
                    release_date=date(2024, 1, 1),
                    msrp_usd=1899,
                    device_category="Gaming",
                    cpu_name="Intel Core Ultra 7 255HX",
                    gpu_name="NVIDIA GeForce RTX 5060",
                    gpu_type="Dedicated",
                    ram_gb=32,
                    storage_gb=1024,
                    display={"size_inch": 16, "resolution": "2560x1600", "refresh_hz": 165},
                    weight_g=2500.0,
                    os="Windows",
                    source_urls=["https://example.com"],
                )
            )
            session.commit()
