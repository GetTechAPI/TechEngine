"""A CPU, GPU or SoC without specs or a date survives validation, seeding, the API and dumps."""

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
# category -> (resource, folder of the bare record, folder of the full one,
#              what stays required, the specs that may be unknown)
CASES: dict[str, tuple[str, str, str, dict[str, Any], dict[str, Any]]] = {
    "cpu": (
        "cpus", "unknown/unknown", "2020/consumer", {},
        {
            "release_date": "2020-01-01",
            "segment": "desktop",
            "architecture": "Example",
            "cores": 8,
            "threads": 16,
            "tdp_w": 4.5,
        },
    ),
    "gpu": (
        "gpus", "unknown/consumer", "2020/consumer", {},
        {
            "release_date": "2020-01-01",
            "architecture": "Example",
            "memory_gb": 8.0,
            "memory_type": "GDDR6",
            "memory_bus_bit": 128,
            "base_clock_mhz": 1000,
            "boost_clock_mhz": 1200,
            "tdp_w": 150,
            "pcie_version": "PCIe 4.0 x16",
        },
    ),
    "soc": (
        "socs", "unknown", "2020", {},
        {"release_date": "2020-01-01", "process_nm": 7.0, "gpu_name": "Example GPU"},
    ),
}


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def _chip(slug: str, specs: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": f"Example {slug}",
        "manufacturer": "example",
        "source_urls": SRC,
        "verified": False,
        **specs,
    }


@pytest.mark.parametrize("category", list(CASES))
@pytest.mark.parametrize("null", [False, True], ids=["omitted", "null"])
def test_chip_without_specs_round_trip(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    null: bool,
) -> None:
    resource, bare_dir, full_dir, required, specs = CASES[category]
    data_dir = tmp_path / "data"
    _write(
        data_dir / "brand" / "us" / "example.json",
        {
            "slug": "example",
            "name": "Example",
            "country": "US",
            "categories": ["cpu-designer"],
            "source_urls": SRC,
        },
    )
    base = data_dir / category / "example"
    unknown = dict.fromkeys(specs) if null else {}
    _write(base / bare_dir / "chip-bare.json", _chip("chip-bare", {**required, **unknown}))
    _write(base / full_dir / "chip-full.json", _chip("chip-full", {**required, **specs}))
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
        response = client.get(f"/v1/{resource}/chip-bare")
        assert response.status_code == 200
        detail = response.json()
        for field in specs:
            assert detail[field] is None
        # Nothing to score from, and nothing raised on the way.
        assert detail["score"]["overall"] is None
        assert client.get(f"/v1/{resource}/chip-bare/score").status_code == 200

        # The full record keeps what it states, fractional watts included.
        full = client.get(f"/v1/{resource}/chip-full").json()
        for field, value in specs.items():
            assert full[field] == value

        listing = client.get(f"/v1/{resource}")
        assert listing.status_code == 200
        assert listing.json()["count"] == 2
        if category == "cpu":
            # A segment filter never matches a CPU whose segment is unknown.
            filtered = client.get("/v1/cpus?segment=desktop").json()
            assert [ref["slug"] for ref in filtered["results"]] == ["chip-full"]

        output_dir = tmp_path / "dump"
        assert generate(client, output_dir, [resource]) == {resource: 2}
        dumped = json.loads(
            (output_dir / "v1" / resource / "chip-bare" / "index.json").read_text("utf-8")
        )
        assert dumped == detail
    finally:
        test_engine.dispose()


@pytest.mark.parametrize("category", list(CASES))
def test_chip_release_date_precision_round_trip(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
) -> None:
    resource, _, full_dir, _, _ = CASES[category]
    data_dir = tmp_path / "data"
    _write(
        data_dir / "brand" / "us" / "example.json",
        {
            "slug": "example",
            "name": "Example",
            "country": "US",
            "categories": ["cpu-designer"],
            "source_urls": SRC,
        },
    )
    base = data_dir / category / "example"
    # A CPU that states no segment lives in the unknown segment folder.
    year_dir = "2012/unknown" if category == "cpu" else full_dir.replace("2020", "2012")
    # What the source states: a full date, "May 2012", "2012".
    chips = {
        "chip-day": ("2012-05-14", None),
        "chip-month": ("2012-05-01", "month"),
        "chip-year": ("2012-01-01", "year"),
    }
    _write(base / year_dir / "chip-day.json", _chip("chip-day", {"release_date": "2012-05-14"}))
    for slug in ("chip-month", "chip-year"):
        released, precision = chips[slug]
        _write(
            base / year_dir / f"{slug}.json",
            _chip(slug, {"release_date": released, "release_date_precision": precision}),
        )
    monkeypatch.setattr(validate, "DATA_DIR", data_dir)
    assert validate.validate() == []

    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'precision.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(test_engine)
    try:
        with Session(test_engine) as session:
            assert seed(session, data_dir)[resource] == 3

        def session_override() -> Iterator[Session]:
            with Session(test_engine) as session:
                yield session

        monkeypatch.setitem(app.dependency_overrides, get_session, session_override)
        output_dir = tmp_path / "dump"
        assert generate(client, output_dir, [resource]) == {resource: 3}
        for slug, (released, precision) in chips.items():
            detail = client.get(f"/v1/{resource}/{slug}").json()
            assert detail["release_date"] == released
            # A record that does not state a precision is served with null.
            assert detail["release_date_precision"] == precision
            dumped = json.loads(
                (output_dir / "v1" / resource / slug / "index.json").read_text("utf-8")
            )
            assert dumped == detail
    finally:
        test_engine.dispose()


def test_chip_specs_openapi_contract(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    for name, (_, _, _, required, specs) in zip(
        ("CPURead", "GPURead", "SoCRead"), CASES.values(), strict=True
    ):
        for field in specs:
            assert field not in schemas[name]["required"]
        for field in required:
            assert field in schemas[name]["required"]
        assert "release_date_precision" in schemas[name]["properties"]
        assert "release_date_precision" not in schemas[name]["required"]
    for field in ("process_nm", "gpu_name"):
        assert field not in schemas["SoCSummary"]["required"]
