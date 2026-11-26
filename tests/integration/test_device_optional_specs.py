"""A device without specs survives validation, seeding, API responses, and dumps."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app import validate
from app.database import get_session
from app.dump import generate
from app.main import app
from app.seed import seed

SRC = ["https://example.com"]
# category -> (resource, the specs that may be unknown)
CASES: dict[str, tuple[str, dict[str, Any]]] = {
    "smartphone": ("smartphones", {"release_date": "2020-01-01", "ram_gb": 4, "os": "Example OS"}),
    "tablet": ("tablets", {"release_date": "2020-01-01", "ram_gb": 4, "os": "Example OS"}),
    "watch": ("watches", {"release_date": "2020-01-01", "ram_gb": 1, "os": "Example OS"}),
    "pda": ("pdas", {"release_date": "2020-01-01", "ram_gb": 1, "os": "Example OS"}),
    "laptop": ("laptops", {"release_date": "2020-01-01", "ram_gb": 16, "os": "Example OS"}),
    "monitor": (
        "monitors",
        {"release_date": "2020-01-01", "size_inch": 27.0, "resolution": "2560x1440"},
    ),
}


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def _device(slug: str, specs: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": slug,
        "base_model_slug": "device",
        "name": f"Example {slug}",
        "brand": "example",
        "source_urls": SRC,
        "verified": False,
        **specs,
    }


@pytest.mark.parametrize("category", list(CASES))
@pytest.mark.parametrize("null", [False, True], ids=["omitted", "null"])
def test_device_without_specs_round_trip(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    null: bool,
) -> None:
    resource, specs = CASES[category]
    data_dir = tmp_path / "data"
    _write(
        data_dir / "brand" / "us" / "example.json",
        {
            "slug": "example",
            "name": "Example",
            "country": "US",
            "categories": ["smartphone-oem"],
            "source_urls": SRC,
        },
    )
    base = data_dir / category / "example"
    unknown = dict.fromkeys(specs) if null else {}
    _write(base / "unknown" / "device" / "device-bare.json", _device("device-bare", unknown))
    _write(base / "2020" / "device" / "device-full.json", _device("device-full", specs))
    monkeypatch.setattr(validate, "DATA_DIR", data_dir)
    assert validate.validate() == []

    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'optional-specs.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(test_engine)
    try:
        with Session(test_engine) as session:
            assert seed(session, data_dir)[resource] == 2

        def session_override() -> Iterator[Session]:
            with Session(test_engine) as session:
                yield session

        monkeypatch.setitem(app.dependency_overrides, get_session, session_override)
        response = client.get(f"/v1/{resource}/device-bare")
        assert response.status_code == 200
        detail = response.json()
        for field in specs:
            assert detail[field] is None
        if category == "smartphone":
            # Nothing to score from, and nothing raised on the way.
            assert detail["score"]["overall"] is None
            assert client.get(f"/v1/{resource}/device-bare/score").status_code == 200

        # A record without the value sorts last in both directions.
        sorts = ["release_date"] + (["size_inch"] if category == "monitor" else [])
        for field in sorts:
            for sort in (field, f"-{field}"):
                listing = client.get(f"/v1/{resource}?sort={sort}")
                assert listing.status_code == 200
                assert [ref["slug"] for ref in listing.json()["results"]] == [
                    "device-full",
                    "device-bare",
                ]

        output_dir = tmp_path / "dump"
        assert generate(client, output_dir, [resource]) == {resource: 2}
        dumped = json.loads(
            (output_dir / "v1" / resource / "device-bare" / "index.json").read_text("utf-8")
        )
        assert dumped == detail
    finally:
        test_engine.dispose()


def test_device_specs_openapi_contract(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    for name in ("SmartphoneRead", "MobileDeviceRead", "LaptopRead"):
        for field in ("release_date", "ram_gb", "os"):
            assert field not in schemas[name]["required"]
    for field in ("release_date", "size_inch", "resolution"):
        assert field not in schemas["MonitorRead"]["required"]
