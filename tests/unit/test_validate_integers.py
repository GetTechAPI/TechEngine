"""Fields the models type as integers must hold integers in the seed JSON."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import NoneType, UnionType
from typing import Any, Union, get_args, get_origin

import pytest
from pydantic import BaseModel

from app import validate
from app.models.brand import Brand
from app.models.cpu import CPU
from app.models.device_catalog import DeviceCatalog
from app.models.gpu import DiscreteGPU
from app.models.laptop import Laptop
from app.models.mobile_device import PDA, Tablet, Watch
from app.models.monitor import Monitor
from app.models.smartphone import Smartphone
from app.models.soc import SoC
from app.models.software import Software
from app.models.website import Website
from app.schemas import (
    BrandRead,
    DeviceCatalogRead,
    LaptopRead,
    MonitorRead,
    SmartphoneRead,
    SoCRead,
    SoftwareRead,
)
from app.schemas.cpu import CPURead
from app.schemas.gpu import GPURead
from app.schemas.mobile_device import MobileDeviceRead
from app.schemas.website import WebsiteRead

REPO = Path(__file__).resolve().parents[2]

MODELS: dict[str, type[BaseModel]] = dict(zip(validate.CATEGORIES, (
    Brand, SoC, Smartphone, Tablet, Watch, PDA, DiscreteGPU, CPU,
    Laptop, Monitor, Software, Website, DeviceCatalog,
), strict=True))
READ_SCHEMAS: dict[str, type[BaseModel]] = {
    "brand": BrandRead, "soc": SoCRead, "smartphone": SmartphoneRead,
    "tablet": MobileDeviceRead, "watch": MobileDeviceRead, "pda": MobileDeviceRead,
    "gpu": GPURead, "cpu": CPURead, "laptop": LaptopRead, "monitor": MonitorRead,
    "software": SoftwareRead, "website": WebsiteRead, "device_catalog": DeviceCatalogRead,
}

# One record path and one integer field per category that has any.
SAMPLES = [
    ("brand/us/acme.json", "founded_year"),
    ("soc/acme/c1.json", "gpu_cores"),
    ("smartphone/acme/2020/p.json", "battery_mah"),
    ("tablet/acme/2020/t/t.json", "battery_mah"),
    ("watch/acme/2020/w/w.json", "msrp_usd"),
    ("pda/acme/2020/d/d.json", "battery_mah"),
    ("gpu/acme/g1.json", "tdp_w"),
    ("cpu/acme/x1.json", "tdp_w"),
    ("laptop/acme/2020/l.json", "ram_gb"),
    ("monitor/acme/2020/m.json", "refresh_hz"),
    ("device_catalog/acme/acme-x.json", "android_sdk_min"),
]


def _put(root: Path, rel: str, rec: dict[str, Any]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec), encoding="utf-8")


def _integer_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rel: str,
                    rec: dict[str, Any], only: set[str] | None = None) -> list[str]:
    monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
    _put(tmp_path, rel, rec)
    return [e for e in validate.validate(only) if "must be an integer" in e
            or "must be a list of integers" in e]


def _integer_fields(model: type[BaseModel]) -> tuple[set[str], set[str]]:
    """Fields a model types as ``int`` and as ``list[int]``.

    Primary and foreign keys are skipped: seed fills them in, the JSON carries
    slugs instead.
    """
    scalars: set[str] = set()
    lists: set[str] = set()
    for field, info in model.model_fields.items():
        if getattr(info, "primary_key", None) is True or isinstance(
            getattr(info, "foreign_key", None), str
        ):
            continue
        annotation = info.annotation
        options = get_args(annotation) if get_origin(annotation) in (Union, UnionType) else (
            annotation,
        )
        kinds = {option for option in options if option is not NoneType}
        if kinds == {int}:
            scalars.add(field)
        elif kinds == {list[int]}:
            lists.add(field)
    return scalars, lists


def test_validator_imports_with_the_standard_library_only() -> None:
    """TechAPI's PR checks run the validator without installing the engine."""
    code = f"import sys; sys.path.insert(0, {str(REPO)!r}); import app.validate"
    # -S keeps site-packages off the path, -I ignores PYTHONPATH and the cwd
    done = subprocess.run([sys.executable, "-I", "-S", "-c", code],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_every_category_is_mapped() -> None:
    assert set(validate.INTEGER_FIELDS) == set(validate.CATEGORIES)


@pytest.mark.parametrize("category", validate.CATEGORIES)
def test_mapping_matches_the_model(category: str) -> None:
    """Fails when a model gains or loses an integer column the validator does not list."""
    assert validate.INTEGER_FIELDS[category] == _integer_fields(MODELS[category])


def test_keys_floats_and_bools_are_not_integer_fields() -> None:
    scalars, _ = _integer_fields(CPU)
    assert not {"id", "manufacturer_id", "base_clock_ghz", "verified"} & scalars
    assert "ram_gb" in _integer_fields(Laptop)[0]
    assert "ram_gb" not in _integer_fields(Smartphone)[0]


@pytest.mark.parametrize("category", validate.CATEGORIES)
def test_read_schema_integers_are_covered(category: str) -> None:
    """A read schema must not type a seed field as int unless the model does."""
    schema_scalars, schema_lists = _integer_fields(READ_SCHEMAS[category])
    scalars, lists = validate.INTEGER_FIELDS[category]
    assert schema_scalars - {"id"} <= scalars
    assert schema_lists <= lists


@pytest.mark.parametrize("rel,field", SAMPLES)
@pytest.mark.parametrize("value", [11.5, True, "12", "12.0", [12], float("nan")])
def test_non_integer_rejected(tmp_path, monkeypatch, rel, field, value) -> None:
    errs = _integer_errors(tmp_path, monkeypatch, rel, {"slug": "s", field: value})
    assert errs == [f"{rel}: {field}={value!r} must be an integer"]


@pytest.mark.parametrize("rel,field", SAMPLES)
@pytest.mark.parametrize("value", [12, 0, 65.0, None])
def test_integer_and_null_accepted(tmp_path, monkeypatch, rel, field, value) -> None:
    assert _integer_errors(tmp_path, monkeypatch, rel, {"slug": "s", field: value}) == []


def test_absent_field_accepted(tmp_path, monkeypatch) -> None:
    assert _integer_errors(tmp_path, monkeypatch, "cpu/acme/x1.json", {"slug": "s"}) == []


def test_float_typed_field_keeps_fractions(tmp_path, monkeypatch) -> None:
    rec = {"slug": "s", "ram_gb": 0.5, "weight_g": 180.5}
    assert _integer_errors(tmp_path, monkeypatch, "smartphone/acme/2020/p.json", rec) == []
    assert _integer_errors(tmp_path, monkeypatch, "cpu/acme/x1.json",
                           {"slug": "s", "base_clock_ghz": 3.5, "l3_cache_mb": 1.5}) == []


@pytest.mark.parametrize("value,ok", [
    ([], True), ([64, 128], True), ([64.0], True), (None, True),
    ([64, 128.5], False), (["64"], False), ([True], False), (64, False), ("64", False),
])
def test_integer_list(tmp_path, monkeypatch, value, ok) -> None:
    rel = "tablet/acme/2020/t/t.json"
    errs = _integer_errors(tmp_path, monkeypatch, rel, {"slug": "s", "storage_options_gb": value})
    expected = [] if ok else [f"{rel}: storage_options_gb={value!r} must be a list of integers"]
    assert errs == expected


def test_scoped_run_checks_changed_and_fk_records_only(tmp_path, monkeypatch) -> None:
    _put(tmp_path, "laptop/acme/2020/old.json", {"slug": "old", "ram_gb": 7.5})
    _put(tmp_path, "cpu/acme/x1.json", {"slug": "x1", "tdp_w": 5.9})
    rel = "laptop/acme/2020/new.json"
    errs = _integer_errors(tmp_path, monkeypatch, rel, {"slug": "new", "ram_gb": 15.5}, {rel})
    # cpu is a foreign-key target, so a scoped run still reads all of it
    assert errs == [
        "cpu/acme/x1.json: tdp_w=5.9 must be an integer",
        f"{rel}: ram_gb=15.5 must be an integer",
    ]
    assert len(_integer_errors(tmp_path, monkeypatch, rel, {"slug": "new"})) == 2
