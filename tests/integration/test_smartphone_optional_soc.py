"""A phone with an unknown chipset survives validation, seeding, API responses, and dumps."""

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
PHONE: dict[str, Any] = {
    "slug": "phone-base",
    "name": "Example phone",
    "brand": "example",
    "release_date": "2020-01-01",
    "ram_gb": 4,
    "battery_mah": 4000,
    "os": "Example OS",
    "source_urls": SRC,
    "verified": False,
}


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def _data_dir(tmp_path: Path, phone: dict[str, Any]) -> Path:
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
    _write(data_dir / "smartphone" / "example" / "2020" / "phone-base.json", phone)
    return data_dir


@pytest.mark.parametrize("soc", [{}, {"soc": None}], ids=["omitted", "null"])
def test_phone_without_soc_round_trip(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    soc: dict[str, None],
) -> None:
    data_dir = _data_dir(tmp_path, {**PHONE, **soc})
    monkeypatch.setattr(validate, "DATA_DIR", data_dir)
    assert validate.validate() == []

    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'optional-soc.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(test_engine)
    try:
        with Session(test_engine) as session:
            assert seed(session, data_dir)["smartphones"] == 1

        def session_override() -> Iterator[Session]:
            with Session(test_engine) as session:
                yield session

        monkeypatch.setitem(app.dependency_overrides, get_session, session_override)
        response = client.get("/v1/smartphones/phone-base")
        assert response.status_code == 200
        detail = response.json()
        assert detail["soc"] is None
        # No chipset means no benchmark: performance is gated, battery still scores.
        assert detail["score"]["performance"] is None
        assert detail["score"]["battery"] is not None

        score = client.get("/v1/smartphones/phone-base/score")
        assert score.status_code == 200
        assert score.json()["performance"] is None

        output_dir = tmp_path / "dump"
        assert generate(client, output_dir, ["smartphones"]) == {"smartphones": 1}
        dumped = json.loads(
            (output_dir / "v1" / "smartphones" / "phone-base" / "index.json").read_text("utf-8")
        )
        assert dumped == detail
    finally:
        test_engine.dispose()


def test_phone_with_unknown_soc_fails_seed(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path, {**PHONE, "soc": "no-such-chip"})
    test_engine = create_engine(f"sqlite:///{tmp_path / 'unknown-soc.db'}")
    SQLModel.metadata.create_all(test_engine)
    try:
        with Session(test_engine) as session, pytest.raises(ValueError, match="unknown SoC"):
            seed(session, data_dir)
    finally:
        test_engine.dispose()


def test_phone_soc_openapi_contract(client: TestClient) -> None:
    phone = client.get("/openapi.json").json()["components"]["schemas"]["SmartphoneRead"]
    assert "soc" not in phone["required"]
