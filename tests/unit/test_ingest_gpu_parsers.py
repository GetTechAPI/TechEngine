"""GPU-specific normalize-helper tests."""

from __future__ import annotations

import pytest

from app.ingest.normalize import (
    guess_gpu_segment,
    parse_frequency_mhz,
    parse_memory_bus_bit,
    parse_memory_gb,
    parse_pcie_version,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2235 MHz", 2235),
        ("2.5 GHz", 2500),
        ("1.0 GHz", 1000),
        ("", None),
    ],
)
def test_parse_frequency_mhz(text: str, expected: int | None) -> None:
    assert parse_frequency_mhz(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("24 GB", 24.0),
        ("4096 MB", 4.0),
        ("8GB", 8.0),
        ("", None),
    ],
)
def test_parse_memory_gb(text: str, expected: float | None) -> None:
    assert parse_memory_gb(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("384-bit", 384),
        ("128 bit", 128),
        ("256bit", 256),
        ("", None),
    ],
)
def test_parse_memory_bus_bit(text: str, expected: int | None) -> None:
    assert parse_memory_bus_bit(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("PCIe 4.0 x16", "4.0"),
        ("PCI-e Gen 5", "5"),
        ("PCIe 3", "3"),
        ("none", None),
    ],
)
def test_parse_pcie_version(text: str, expected: str | None) -> None:
    assert parse_pcie_version(text) == expected


def test_guess_gpu_segment() -> None:
    assert guess_gpu_segment("NVIDIA GeForce RTX 5090") == "consumer"
    assert guess_gpu_segment("NVIDIA H100 SXM5") == "enterprise"
    assert guess_gpu_segment("NVIDIA RTX 6000 Ada") == "enterprise"
    assert guess_gpu_segment("AMD Radeon RX 7900 XTX") == "consumer"
    assert guess_gpu_segment("AMD Instinct MI300X") == "enterprise"
    assert guess_gpu_segment("Intel Arc B580") == "consumer"
