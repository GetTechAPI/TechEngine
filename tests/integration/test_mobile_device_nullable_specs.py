"""Unknown mobile specs survive validation, seeding, API responses, and dumps."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app import validate
from app.database import get_session
from app.dump import generate
from app.main import app
from app.schemas.mobile_device import MobileDeviceRead
from app.seed import seed


@pytest.mark.parametrize(
    "category,resource", [("tablet", "tablets"), ("watch", "watches"), ("pda", "pdas")]
)
@pytest.mark.parametrize(
    "specs",
    [
        {},
        {"battery_mah": None, "weight_g": None},
        {"battery_mah": None, "weight_g": 150.0},
        {"battery_mah": 1500, "weight_g": None},
        {"battery_mah": 1500, "weight_g": 150.0},
    ],
    ids=["omitted", "null", "unknown-battery", "unknown-weight", "known"],
)
def test_mobile_specs_round_trip(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    resource: str,
    specs: dict[str, int | float | None],
) -> None:
    data_dir = tmp_path / "data"
    brand_path = data_dir / "brand" / "us" / "example.json"
    brand_path.parent.mkdir(parents=True)
    brand_path.write_text(
        json.dumps(
            {
                "slug": "example",
                "name": "Example",
                "country": "US",
                "categories": ["smartphone-oem"],
                "source_urls": ["https://example.com"],
            }
        ),
        encoding="utf-8",
    )
    device_path = data_dir / category / "example" / "2020" / "device" / "device-base.json"
    device_path.parent.mkdir(parents=True)
    device_path.write_text(
        json.dumps(
            {
                "slug": "device-base",
                "base_model_slug": "device",
                "name": "Example device",
                "brand": "example",
                "release_date": "2020-01-01",
                "ram_gb": 1,
                "os": "Example OS",
                "source_urls": ["https://example.com"],
                "verified": False,
                **specs,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(validate, "DATA_DIR", data_dir)
    assert validate.validate() == []

    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'nullable.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(test_engine)
    try:
        with Session(test_engine) as session:
            assert seed(session, data_dir)[resource] == 1

        def session_override() -> Iterator[Session]:
            with Session(test_engine) as session:
                yield session

        monkeypatch.setitem(app.dependency_overrides, get_session, session_override)
        response = client.get(f"/v1/{resource}/device-base")
        assert response.status_code == 200
        detail = response.json()
        for field in ("battery_mah", "weight_g"):
            assert detail[field] == specs.get(field)

        # The response schema also defaults omitted values to None.
        without_specs = {k: v for k, v in detail.items() if k not in ("battery_mah", "weight_g")}
        read = MobileDeviceRead.model_validate(without_specs)
        assert read.battery_mah is None
        assert read.weight_g is None

        output_dir = tmp_path / "dump"
        assert generate(client, output_dir, [resource]) == {resource: 1}
        dumped = json.loads(
            (output_dir / "v1" / resource / "device-base" / "index.json").read_text("utf-8")
        )
        assert dumped == detail
    finally:
        test_engine.dispose()


def test_mobile_specs_openapi_contract(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    mobile = schemas["MobileDeviceRead"]
    phone = schemas["SmartphoneRead"]
    for field, numeric_type in (("battery_mah", "integer"), ("weight_g", "number")):
        assert field not in mobile["required"]
        assert {variant["type"] for variant in mobile["properties"][field]["anyOf"]} == {
            numeric_type,
            "null",
        }
        assert field in phone["required"]
        assert phone["properties"][field]["type"] == numeric_type
