"""Device catalog (identity-only) endpoints and the identity fields on device records."""

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
from tests.integration.device_catalog_fixtures import ensure_device_catalog_fixtures

SRC = ["https://example.com/supported_devices.csv"]


def test_list_filters_by_base_model(client: TestClient) -> None:
    ensure_device_catalog_fixtures()
    body = client.get(
        "/v1/device-catalog", params={"base_model_slug": "catalog-test-phone-x1-base"}
    ).json()
    assert body["count"] == 1
    assert body["results"][0]["url"].endswith("/v1/device-catalog/catalog-test-phone-x1")


def test_list_filters_by_brand_and_form_factor(client: TestClient) -> None:
    ensure_device_catalog_fixtures()
    params = {"brand": "catalog-test-oem", "form_factor": "phone", "sort": "-android_sdk_min"}
    body = client.get("/v1/device-catalog", params=params).json()
    assert [r["slug"] for r in body["results"]] == ["catalog-test-phone-x1"]
    missing = client.get("/v1/device-catalog", params={"brand": "no-such-brand"}).json()
    assert missing["count"] == 0


def test_detail(client: TestClient) -> None:
    ensure_device_catalog_fixtures()
    body = client.get("/v1/device-catalog/catalog-test-phone-x1").json()
    assert body["brand"]["slug"] == "catalog-test-oem"
    assert body["brand"]["country"] is None
    assert body["model_numbers"] == ["CT-X1A", "CT-X1B"]
    assert body["codenames"] == ["ctx1"]
    assert body["form_factor"] == "phone"
    assert body["device_type_guess"] is None
    assert body["promoted_to"] is None
    assert "score" not in body


def test_sort_rejects_unknown_field_and_404(client: TestClient) -> None:
    ensure_device_catalog_fixtures()
    assert client.get("/v1/device-catalog", params={"sort": "nope"}).status_code == 400
    assert client.get("/v1/device-catalog/nonexistent").status_code == 404


def _put(root: Path, rel: str, rec: dict[str, Any]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec), encoding="utf-8")


def test_identity_records_round_trip(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """validate -> seed -> API -> dump, with nothing invented for unknown fields."""
    data = tmp_path / "data"
    _put(data, "brand/unknown/nocountry.json", {
        "slug": "nocountry", "name": "No Country", "country": None,
        "categories": ["smartphone-oem"], "source_urls": SRC,
    })
    _put(data, "brand/us/chipco.json", {
        "slug": "chipco", "name": "ChipCo", "country": "US",
        "categories": ["soc-designer"], "source_urls": SRC,
    })
    _put(data, "soc/chipco/chip-1.json", {
        "slug": "chip-1", "name": "Chip 1", "manufacturer": "chipco",
        "release_date": "2021-01-01", "process_nm": 7, "gpu_name": "G1", "source_urls": SRC,
    })
    identity = {"model_numbers": ["NC-100"], "codenames": ["nc100"],
                "release_date_precision": "year_estimated"}
    _put(data, "smartphone/nocountry/2021/nc-phone.json", {
        "slug": "nc-phone", "name": "NC Phone", "brand": "nocountry", "soc": "chip-1",
        "release_date": "2021-01-01", "ram_gb": 4, "os": "Android",
        "battery_mah": None, "source_urls": SRC, **identity,
    })
    _put(data, "tablet/nocountry/2021/nc-tab/nc-tab.json", {
        "slug": "nc-tab", "base_model_slug": "nc-tab", "name": "NC Tab", "brand": "nocountry",
        "release_date": "2021-01-01", "ram_gb": 4, "os": "Android",
        "source_urls": SRC, "verified": False, **identity,
    })
    _put(data, "device_catalog/nocountry/nc-watch.json", {
        "slug": "nc-watch", "name": "NC Watch", "brand": "nocountry",
        "codenames": ["ncw"], "form_factor": "watch", "android_sdk_min": 30,
        "promoted_to": "watch/nc-watch-lte", "source_urls": SRC, "verified": False,
    })
    _put(data, "device_catalog/nocountry/nc-pad.json", {
        "slug": "nc-pad", "name": "NC Pad", "brand": "nocountry", "soc": "chip-1",
        "soc_raw": "Chip 1", "gpu_raw": "G1", "ram_gb": 3.5, "screen_resolution": "1200x2000",
        "screen_density_dpi": 240, "android_sdk_min": 30, "android_sdk_max": 33,
        "release_year": 2021, "release_year_source": "model_code",
        "source_urls": SRC, "verified": False,
    })
    monkeypatch.setattr(validate, "DATA_DIR", data)
    assert validate.validate() == []

    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'catalog.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(test_engine)
    try:
        with Session(test_engine) as session:
            counts = seed(session, data)
        assert (counts["smartphones"], counts["tablets"], counts["device-catalog"]) == (1, 1, 2)

        def session_override() -> Iterator[Session]:
            with Session(test_engine) as session:
                yield session

        monkeypatch.setitem(app.dependency_overrides, get_session, session_override)
        phone = client.get("/v1/smartphones/nc-phone").json()
        tablet = client.get("/v1/tablets/nc-tab").json()
        for body in (phone, tablet):
            for field, value in identity.items():
                assert body[field] == value
        assert phone["battery_mah"] is None and phone["weight_g"] is None
        assert phone["score"]["battery"] is None
        assert phone["brand"]["country"] is None

        entry = client.get("/v1/device-catalog/nc-watch").json()
        assert entry["model_numbers"] == [] and entry["marketing_names"] == []
        assert entry["promoted_to"] == "watch/nc-watch-lte"
        assert entry["soc"] is None and entry["ram_gb"] is None and entry["release_year"] is None

        pad = client.get("/v1/device-catalog/nc-pad").json()
        assert pad["soc"]["slug"] == "chip-1"
        assert pad["soc"]["manufacturer"]["slug"] == "chipco"
        assert (pad["ram_gb"], pad["screen_resolution"], pad["screen_density_dpi"]) == (
            3.5, "1200x2000", 240)
        assert (pad["android_sdk_min"], pad["android_sdk_max"]) == (30, 33)
        assert (pad["release_year"], pad["release_year_source"]) == (2021, "model_code")

        out = tmp_path / "dump"
        assert generate(client, out, ["device-catalog"]) == {"device-catalog": 2}
        dumped = json.loads((out / "v1/device-catalog/nc-watch/index.json").read_text("utf-8"))
        assert dumped == entry
    finally:
        test_engine.dispose()
