"""Sockets and chipsets: validation, seeding, API responses, and dumps."""

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


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def _brand(slug: str) -> dict[str, Any]:
    return {"slug": slug, "name": slug.title(), "country": "US",
            "categories": ["chipset-maker"], "source_urls": SRC}


def _part(slug: str, name: str, **fields: Any) -> dict[str, Any]:
    return {"slug": slug, "name": name, "verified": False, "source_urls": SRC, **fields}


@pytest.fixture
def parts_client(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, Path]]:
    data_dir = tmp_path / "data"
    for brand in ("example", "other"):
        _write(data_dir / "brand" / "us" / f"{brand}.json", _brand(brand))
    _write(data_dir / "socket" / "ex1.json", _part(
        "ex1", "EX 1", manufacturer="example", aliases=["Socket E"], release_date="2020",
        package_type="LGA", pin_count=1200))
    _write(data_dir / "socket" / "ex2.json", _part(
        "ex2", "EX 2", manufacturer="other", release_date="2019-06-01"))
    _write(data_dir / "socket" / "shared.json", _part("shared", "Shared", manufacturer=None))
    _write(data_dir / "chipset" / "example" / "example-a1.json", _part(
        "example-a1", "A1", manufacturer="example", aliases=["Alpha"], series="A series",
        release_date="2020-05", sockets=["ex2", "ex1"], segment="desktop",
        cpu_overclocking=True, memory_types=["DDR4"], max_memory_gb=128, pcie_version="4.0",
        usb_ports={"USB 2.0": 6, "USB 3.2 Gen 1x1": 4}, sata_ports=6, tdp_w=6.5, process_nm=14))
    _write(data_dir / "chipset" / "other" / "other-b1.json", _part(
        "other-b1", "B1", manufacturer="other"))
    cpus = data_dir / "cpu" / "example" / "unknown" / "unknown"
    _write(cpus / "example-c1.json", {
        "slug": "example-c1", "name": "C1", "manufacturer": "example", "socket": "Socket E",
        "socket_slug": "ex1", "source_urls": SRC})
    _write(cpus / "example-c2.json", {
        "slug": "example-c2", "name": "C2", "manufacturer": "example", "socket": "BGA 900",
        "source_urls": SRC})
    monkeypatch.setattr(validate, "DATA_DIR", data_dir)
    assert validate.validate() == []

    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'parts.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(test_engine)
    try:
        with Session(test_engine) as session:
            counts = seed(session, data_dir)
            assert (counts["sockets"], counts["chipsets"]) == (3, 2)
            assert seed(session, data_dir)["sockets"] == 0  # idempotent

        def session_override() -> Iterator[Session]:
            with Session(test_engine) as session:
                yield session

        monkeypatch.setitem(app.dependency_overrides, get_session, session_override)
        yield client, tmp_path
    finally:
        test_engine.dispose()


def _slugs(client: TestClient, url: str) -> list[str]:
    response = client.get(url)
    assert response.status_code == 200
    return [ref["slug"] for ref in response.json()["results"]]


def test_socket_endpoints(parts_client: tuple[TestClient, Path]) -> None:
    client, _ = parts_client
    assert _slugs(client, "/v1/sockets") == ["ex1", "ex2", "shared"]
    assert _slugs(client, "/v1/sockets?manufacturer=example") == ["ex1"]
    assert _slugs(client, "/v1/sockets?manufacturer=nobody") == []
    # A socket without a date sorts last in both directions.
    assert _slugs(client, "/v1/sockets?sort=release_date") == ["ex2", "ex1", "shared"]
    assert _slugs(client, "/v1/sockets?sort=-release_date") == ["ex1", "ex2", "shared"]
    assert client.get("/v1/sockets?sort=nope").status_code == 400

    detail = client.get("/v1/sockets/ex1").json()
    assert detail["manufacturer"] == {
        "slug": "example", "name": "Example", "url": "/v1/brands/example"
    }
    assert detail["aliases"] == ["Socket E"]
    assert detail["release_date"] == "2020"  # kept as written, not padded to a day
    assert (detail["package_type"], detail["pin_count"]) == ("LGA", 1200)
    assert detail["url"] == "/v1/sockets/ex1"
    assert "score" not in detail

    shared = client.get("/v1/sockets/shared").json()
    assert shared["manufacturer"] is None
    assert shared["aliases"] == []
    for field in ("release_date", "package_type", "pin_count"):
        assert shared[field] is None
    assert client.get("/v1/sockets/nope").status_code == 404


def test_cpu_socket_link(parts_client: tuple[TestClient, Path]) -> None:
    client, tmp_path = parts_client
    linked = client.get("/v1/cpus/example-c1").json()
    # The text stays as the source wrote it; the slug joins to the socket record.
    assert (linked["socket"], linked["socket_slug"]) == ("Socket E", "ex1")
    assert client.get(f"/v1/sockets/{linked['socket_slug']}").json()["name"] == "EX 1"
    unlinked = client.get("/v1/cpus/example-c2").json()
    assert (unlinked["socket"], unlinked["socket_slug"]) == ("BGA 900", None)

    assert _slugs(client, "/v1/cpus") == ["example-c1", "example-c2"]
    assert _slugs(client, "/v1/cpus?socket_slug=ex1") == ["example-c1"]
    assert _slugs(client, "/v1/cpus?socket_slug=ex2") == []

    output_dir = tmp_path / "dump"
    assert generate(client, output_dir, ["cpus"]) == {"cpus": 2}
    dumped = json.loads(
        (output_dir / "v1" / "cpus" / "example-c1" / "index.json").read_text("utf-8")
    )
    assert dumped == linked

    schema = client.get("/openapi.json").json()["components"]["schemas"]["CPURead"]
    assert "socket_slug" in schema["properties"]
    assert "socket_slug" not in schema["required"]


def test_seed_rejects_unknown_cpu_socket(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write(data_dir / "brand" / "us" / "example.json", _brand("example"))
    _write(data_dir / "cpu" / "example" / "unknown" / "unknown" / "example-c1.json", {
        "slug": "example-c1", "name": "C1", "manufacturer": "example",
        "socket_slug": "nope", "source_urls": SRC})
    test_engine = create_engine(f"sqlite:///{tmp_path / 'bad.db'}")
    SQLModel.metadata.create_all(test_engine)
    try:
        with Session(test_engine) as session, pytest.raises(ValueError, match="unknown socket"):
            seed(session, data_dir)
    finally:
        test_engine.dispose()


def test_chipset_endpoints(parts_client: tuple[TestClient, Path]) -> None:
    client, _ = parts_client
    assert _slugs(client, "/v1/chipsets") == ["example-a1", "other-b1"]
    assert _slugs(client, "/v1/chipsets?manufacturer=other") == ["other-b1"]
    assert _slugs(client, "/v1/chipsets?manufacturer=nobody") == []
    assert _slugs(client, "/v1/chipsets?sort=-release_date") == ["example-a1", "other-b1"]
    assert client.get("/v1/chipsets?sort=nope").status_code == 400

    detail = client.get("/v1/chipsets/example-a1").json()
    assert detail["manufacturer"]["slug"] == "example"
    assert detail["sockets"] == [
        {"slug": "ex2", "name": "EX 2", "url": "/v1/sockets/ex2"},
        {"slug": "ex1", "name": "EX 1", "url": "/v1/sockets/ex1"},
    ]
    assert detail["release_date"] == "2020-05"
    assert detail["usb_ports"] == {"USB 2.0": 6, "USB 3.2 Gen 1x1": 4}
    assert detail["memory_types"] == ["DDR4"]
    assert (detail["sata_ports"], detail["tdp_w"], detail["cpu_overclocking"]) == (6, 6.5, True)
    assert "score" not in detail

    bare = client.get("/v1/chipsets/other-b1").json()
    assert (bare["aliases"], bare["sockets"]) == ([], [])
    for field in ("series", "release_date", "segment", "cpu_overclocking", "memory_types",
                  "max_memory_gb", "pcie_version", "usb_ports", "sata_ports", "tdp_w",
                  "process_nm"):
        assert bare[field] is None
    assert client.get("/v1/chipsets/nope").status_code == 404


def test_parts_dump(parts_client: tuple[TestClient, Path]) -> None:
    client, tmp_path = parts_client
    output_dir = tmp_path / "dump"
    assert generate(client, output_dir, ["sockets", "chipsets"]) == {"sockets": 3, "chipsets": 2}
    for resource, slug in (("sockets", "shared"), ("chipsets", "example-a1")):
        dumped = json.loads(
            (output_dir / "v1" / resource / slug / "index.json").read_text("utf-8")
        )
        assert dumped == client.get(f"/v1/{resource}/{slug}").json()
        assert not (output_dir / "v1" / resource / slug / "score").exists()
    manifest = json.loads((output_dir / "v1" / "index.json").read_text("utf-8"))
    assert manifest["collections"]["chipsets"] == {"count": 2, "url": "/v1/chipsets/index.json"}


def test_seed_rejects_an_unknown_socket(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write(data_dir / "brand" / "us" / "example.json", _brand("example"))
    _write(data_dir / "chipset" / "example" / "example-a1.json", _part(
        "example-a1", "A1", manufacturer="example", sockets=["missing"]))
    test_engine = create_engine(f"sqlite:///{tmp_path / 'bad.db'}")
    SQLModel.metadata.create_all(test_engine)
    try:
        with Session(test_engine) as session, pytest.raises(ValueError, match="unknown socket"):
            seed(session, data_dir)
    finally:
        test_engine.dispose()


def test_collections_without_a_data_folder(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    _write(data_dir / "brand" / "us" / "example.json", _brand("example"))
    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'empty.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(test_engine)
    try:
        with Session(test_engine) as session:
            counts = seed(session, data_dir)
            assert (counts["sockets"], counts["chipsets"]) == (0, 0)

        def session_override() -> Iterator[Session]:
            with Session(test_engine) as session:
                yield session

        monkeypatch.setitem(app.dependency_overrides, get_session, session_override)
        for resource in ("sockets", "chipsets"):
            body = client.get(f"/v1/{resource}").json()
            assert (body["count"], body["results"]) == (0, [])
        assert generate(client, tmp_path / "dump", ["sockets", "chipsets"]) == {
            "sockets": 0, "chipsets": 0,
        }
    finally:
        test_engine.dispose()
