"""Seed the database from JSON files in ``data/`` (§0.5.2 step 6, §9.1 Phase 0).

Relations are expressed by slug in the JSON (human-friendly) and resolved to
foreign keys here:

* ``data/brands/*.json``      → Brand
* ``data/socs/*.json``        → SoC (``manufacturer`` = brand slug)
* ``data/smartphones/*.json`` → Smartphone (``brand`` + ``soc`` slugs)

Run with: ``python -m app.seed``

Data is sourced from the sibling TechAPI checkout by default; override with
the ``TECHAPI_DATA_DIR`` environment variable.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from sqlmodel import Session, SQLModel, select

from app.data_root import get_data_root
from app.database import create_db_and_tables, engine
from app.models.brand import Brand
from app.models.cpu import CPU
from app.models.gpu import DiscreteGPU
from app.models.laptop import Laptop
from app.models.mobile_device import PDA, Tablet, Watch
from app.models.smartphone import Smartphone
from app.models.soc import SoC

DATA_DIR = get_data_root()


def _load_dir(subdir: Path) -> list[dict[str, Any]]:
    if not subdir.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(subdir.rglob("*.json")):  # recurse into brand subfolders
        record = json.loads(path.read_text(encoding="utf-8"))
        # SQLModel table models skip validation, so coerce ISO date strings here.
        if isinstance(record.get("release_date"), str):
            record["release_date"] = date.fromisoformat(record["release_date"])
        items.append(record)
    return items


def _existing_slugs(session: Session, model: type[SQLModel]) -> set[str]:
    rows = session.exec(select(model)).all()
    return {row.slug for row in rows}  # type: ignore[attr-defined]  # all data models have slug


def seed(session: Session, data_dir: Path = DATA_DIR) -> dict[str, int]:
    """Idempotently insert seed data. Returns counts of newly inserted rows."""
    counts = {
        "brands": 0,
        "socs": 0,
        "smartphones": 0,
        "tablets": 0,
        "watches": 0,
        "pdas": 0,
        "gpus": 0,
        "cpus": 0,
        "laptops": 0,
    }

    # --- Brands ---
    brand_slugs = _existing_slugs(session, Brand)
    for record in _load_dir(data_dir / "brand"):
        if record["slug"] in brand_slugs:
            continue
        # `categories` lives in the JSON for browsing/validation only — the Brand
        # table model does not (yet) carry it, so drop before construction.
        record.pop("categories", None)
        session.add(Brand(**record))
        counts["brands"] += 1
    session.commit()

    brand_id_by_slug = {b.slug: b.id for b in session.exec(select(Brand)).all()}

    # --- SoCs ---
    soc_slugs = _existing_slugs(session, SoC)
    for record in _load_dir(data_dir / "soc"):
        if record["slug"] in soc_slugs:
            continue
        manufacturer = record.pop("manufacturer")
        manufacturer_id = brand_id_by_slug.get(manufacturer)
        if manufacturer_id is None:
            raise ValueError(
                f"SoC '{record['slug']}' references unknown brand '{manufacturer}'"
            )
        session.add(SoC(manufacturer_id=manufacturer_id, **record))
        counts["socs"] += 1
    session.commit()

    soc_id_by_slug = {s.slug: s.id for s in session.exec(select(SoC)).all()}

    # --- Smartphones ---
    phone_slugs = _existing_slugs(session, Smartphone)
    for record in _load_dir(data_dir / "smartphone"):
        if record["slug"] in phone_slugs:
            continue
        brand_slug = record.pop("brand")
        soc_slug = record.pop("soc")
        brand_id = brand_id_by_slug.get(brand_slug)
        soc_id = soc_id_by_slug.get(soc_slug)
        if brand_id is None:
            raise ValueError(
                f"Smartphone '{record['slug']}' references unknown brand '{brand_slug}'"
            )
        if soc_id is None:
            raise ValueError(
                f"Smartphone '{record['slug']}' references unknown SoC '{soc_slug}'"
            )
        session.add(Smartphone(brand_id=brand_id, soc_id=soc_id, **record))
        counts["smartphones"] += 1
    session.commit()

    def seed_mobile_devices(subdir: str, model: type[SQLModel], count_key: str) -> None:
        device_slugs = _existing_slugs(session, model)
        for record in _load_dir(data_dir / subdir):
            if record["slug"] in device_slugs:
                continue
            brand_slug = record.pop("brand")
            soc_slug = record.pop("soc", None)
            brand_id = brand_id_by_slug.get(brand_slug)
            soc_id = soc_id_by_slug.get(soc_slug) if soc_slug else None
            if brand_id is None:
                raise ValueError(
                    f"{subdir.rstrip('s').title()} '{record['slug']}' "
                    f"references unknown brand '{brand_slug}'"
                )
            if soc_slug and soc_id is None:
                raise ValueError(
                    f"{subdir.rstrip('s').title()} '{record['slug']}' "
                    f"references unknown SoC '{soc_slug}'"
                )
            session.add(model(brand_id=brand_id, soc_id=soc_id, **record))
            counts[count_key] += 1
        session.commit()

    seed_mobile_devices("tablet", Tablet, "tablets")
    seed_mobile_devices("watch", Watch, "watches")
    seed_mobile_devices("pda", PDA, "pdas")

    # --- Discrete GPUs ---
    gpu_slugs = _existing_slugs(session, DiscreteGPU)
    for record in _load_dir(data_dir / "gpu"):
        if record["slug"] in gpu_slugs:
            continue
        manufacturer = record.pop("manufacturer")
        manufacturer_id = brand_id_by_slug.get(manufacturer)
        if manufacturer_id is None:
            raise ValueError(
                f"GPU '{record['slug']}' references unknown brand '{manufacturer}'"
            )
        session.add(DiscreteGPU(manufacturer_id=manufacturer_id, **record))
        counts["gpus"] += 1
    session.commit()

    # --- CPUs ---
    cpu_slugs = _existing_slugs(session, CPU)
    for record in _load_dir(data_dir / "cpu"):
        if record["slug"] in cpu_slugs:
            continue
        manufacturer = record.pop("manufacturer")
        manufacturer_id = brand_id_by_slug.get(manufacturer)
        if manufacturer_id is None:
            raise ValueError(
                f"CPU '{record['slug']}' references unknown brand '{manufacturer}'"
            )
        session.add(CPU(manufacturer_id=manufacturer_id, **record))
        counts["cpus"] += 1
    session.commit()

    # --- Laptops (reference brand [required], cpu + gpu [optional]) ---
    cpu_id_by_slug = {c.slug: c.id for c in session.exec(select(CPU)).all()}
    gpu_id_by_slug = {g.slug: g.id for g in session.exec(select(DiscreteGPU)).all()}
    laptop_slugs = _existing_slugs(session, Laptop)
    for record in _load_dir(data_dir / "laptop"):
        if record["slug"] in laptop_slugs:
            continue
        brand_slug = record.pop("brand")
        cpu_slug = record.pop("cpu", None)
        gpu_slug = record.pop("gpu", None)
        brand_id = brand_id_by_slug.get(brand_slug)
        if brand_id is None:
            raise ValueError(
                f"Laptop '{record['slug']}' references unknown brand '{brand_slug}'"
            )
        cpu_id = cpu_id_by_slug.get(cpu_slug) if cpu_slug else None
        gpu_id = gpu_id_by_slug.get(gpu_slug) if gpu_slug else None
        if cpu_slug and cpu_id is None:
            raise ValueError(
                f"Laptop '{record['slug']}' references unknown CPU '{cpu_slug}'"
            )
        if gpu_slug and gpu_id is None:
            raise ValueError(
                f"Laptop '{record['slug']}' references unknown GPU '{gpu_slug}'"
            )
        session.add(Laptop(brand_id=brand_id, cpu_id=cpu_id, gpu_id=gpu_id, **record))
        counts["laptops"] += 1
    session.commit()

    return counts


def run() -> None:
    create_db_and_tables()
    with Session(engine) as session:
        counts = seed(session)
    total = sum(counts.values())
    print(f"Seeded {total} new records: {counts}")


if __name__ == "__main__":
    run()
