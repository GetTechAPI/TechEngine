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

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from sqlmodel import Session, SQLModel, select

from app.data_root import get_data_root
from app.database import create_db_and_tables, engine
from app.models.brand import Brand
from app.models.cpu import CPU
from app.models.game import Game
from app.models.gpu import DiscreteGPU
from app.models.laptop import Laptop
from app.models.mobile_device import PDA, Tablet, Watch
from app.models.monitor import Monitor
from app.models.smartphone import Smartphone
from app.models.soc import SoC
from app.models.software import Software
from app.models.website import Website

DATA_DIR = get_data_root()


def _load_dir(subdir: Path) -> list[dict[str, Any]]:
    if not subdir.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(subdir.rglob("*.json")):  # recurse into brand subfolders
        record = json.loads(path.read_text(encoding="utf-8"))
        # SQLModel table models skip validation, so coerce ISO date strings here.
        # Keyed on the *_date suffix rather than one field name: a category whose
        # date column is not called release_date (website.launch_date) would
        # otherwise reach SQLite as a str and fail the insert.
        for key, value in list(record.items()):
            if key.endswith("_date") and isinstance(value, str):
                record[key] = date.fromisoformat(value)
        items.append(record)
    return items


# Primary keys are derived from the slug instead of an autoincrement counter.
# The dump exposes `id`, so with a counter one inserted record renumbered every
# row after it and the regenerated dump rewrote pages whose data never changed
# (TechAPI #180: ~1M files, GitHub could not even render the diff).
_ID_BITS = 48  # < 2**53, so the value survives JSON round-trips intact


def _stable_id(table: str, slug: str, taken: set[int]) -> int:
    """Deterministic id for ``table``/``slug``, avoiding ids already assigned."""
    attempt = 0
    while True:
        key = f"{table}:{slug}" if attempt == 0 else f"{table}:{slug}#{attempt}"
        digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big") % (1 << _ID_BITS) or 1
        if value not in taken:
            taken.add(value)
            return value
        attempt += 1  # collision: rehash rather than fall back to a counter


def _with_id(obj: Any, taken: set[int]) -> Any:
    """Stamp a slug-derived id. Every data model has ``slug``; none are typed alike."""
    obj.id = _stable_id(str(type(obj).__tablename__), obj.slug, taken)
    return obj


def _existing_slugs(session: Session, model: type[SQLModel]) -> set[str]:
    rows = session.exec(select(model)).all()
    return {row.slug for row in rows}  # type: ignore[attr-defined]  # all data models have slug


def seed(session: Session, data_dir: Path = DATA_DIR) -> dict[str, int]:
    """Idempotently insert seed data. Returns counts of newly inserted rows."""
    # Ids assigned in this run; only used to break hash collisions.
    taken: set[int] = set()
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
        "monitors": 0,
        "games": 0,
        "software": 0,
        "websites": 0,
    }

    # --- Brands ---
    brand_slugs = _existing_slugs(session, Brand)
    for record in _load_dir(data_dir / "brand"):
        if record["slug"] in brand_slugs:
            continue
        # `categories` lives in the JSON for browsing/validation only — the Brand
        # table model does not (yet) carry it, so drop before construction.
        record.pop("categories", None)
        session.add(_with_id(Brand(**record), taken))
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
        session.add(_with_id(SoC(manufacturer_id=manufacturer_id, **record), taken))
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
        phone = Smartphone(brand_id=brand_id, soc_id=soc_id, **record)
        session.add(_with_id(phone, taken))
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
            device = model(brand_id=brand_id, soc_id=soc_id, **record)
            session.add(_with_id(device, taken))
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
        session.add(_with_id(DiscreteGPU(manufacturer_id=manufacturer_id, **record), taken))
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
        session.add(_with_id(CPU(manufacturer_id=manufacturer_id, **record), taken))
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
        laptop = Laptop(brand_id=brand_id, cpu_id=cpu_id, gpu_id=gpu_id, **record)
        session.add(_with_id(laptop, taken))
        counts["laptops"] += 1
    session.commit()

    # --- Monitors (reference brand [required]) ---
    monitor_slugs = _existing_slugs(session, Monitor)
    for record in _load_dir(data_dir / "monitor"):
        if record["slug"] in monitor_slugs:
            continue
        brand_slug = record.pop("brand")
        brand_id = brand_id_by_slug.get(brand_slug)
        if brand_id is None:
            raise ValueError(
                f"Monitor '{record['slug']}' references unknown brand '{brand_slug}'"
            )
        session.add(_with_id(Monitor(brand_id=brand_id, **record), taken))
        counts["monitors"] += 1
    session.commit()

    # --- Games (standalone; no brand FK) ---
    game_slugs = _existing_slugs(session, Game)
    for record in _load_dir(data_dir / "game"):
        if record["slug"] in game_slugs:
            continue
        session.add(_with_id(Game(**record), taken))
        counts["games"] += 1
    session.commit()

    # --- Software (standalone; no brand FK) ---
    software_slugs = _existing_slugs(session, Software)
    for record in _load_dir(data_dir / "software"):
        if record["slug"] in software_slugs:
            continue
        session.add(_with_id(Software(**record), taken))
        counts["software"] += 1
    session.commit()

    # --- Websites (standalone; no brand FK) ---
    website_slugs = _existing_slugs(session, Website)
    for record in _load_dir(data_dir / "website"):
        if record["slug"] in website_slugs:
            continue
        session.add(_with_id(Website(**record), taken))
        counts["websites"] += 1
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
