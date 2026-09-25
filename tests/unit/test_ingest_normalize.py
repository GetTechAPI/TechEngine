"""Free-text → typed-value parser tests."""

from __future__ import annotations

from datetime import date

import pytest

from app.ingest.normalize import (
    guess_cpu_segment,
    parse_cache_mb,
    parse_cores_threads,
    parse_date,
    parse_frequency_ghz,
    parse_frequency_range_ghz,
    parse_int,
    parse_tdp_w,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("3.0 GHz", 3.0),
        ("3000 MHz", 3.0),
        ("5.8 GHz Turbo", 5.8),
        ("", None),
        ("unknown", None),
    ],
)
def test_parse_frequency_ghz(text: str, expected: float | None) -> None:
    assert parse_frequency_ghz(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("65 W", 65),
        ("65/95 W", 65),
        ("125W", 125),
        ("none", None),
        # Regression: "9.5 W" used to parse as 5 (the digits after the dot).
        ("9.5 W", 10),
        ("3.6 W", 4),
        ("2.2/3 W", 2),
    ],
)
def test_parse_tdp_w(text: str, expected: int | None) -> None:
    assert parse_tdp_w(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1.7–2.0 GHz", (1.7, 2.0)),
        ("1.7-2.0 GHz", (1.7, 2.0)),
        ("1600–2400 MHz", (1.6, 2.4)),
        ("2.0 GHz", None),
        ("2.0–1.7 GHz", None),
        ("", None),
    ],
)
def test_parse_frequency_range_ghz(text: str, expected: tuple[float, float] | None) -> None:
    assert parse_frequency_range_ghz(text) == expected


def test_parse_date_month_year_keeps_the_month() -> None:
    assert parse_date("September 2013") == date(2013, 9, 1)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("30 MB", 30.0),
        ("8 MB", 8.0),
        ("512 KB", 0.5),
        ("1 GB", 1024.0),
        ("", None),
    ],
)
def test_parse_cache_mb(text: str, expected: float | None) -> None:
    assert parse_cache_mb(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("8 / 16", (8, 16)),
        ("16", (16, None)),
        ("24/32", (24, 32)),
        ("", (None, None)),
    ],
)
def test_parse_cores_threads(text: str, expected: tuple[int | None, int | None]) -> None:
    assert parse_cores_threads(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2023-10-17", date(2023, 10, 17)),
        ("October 17, 2023", date(2023, 10, 17)),
        ("17 October 2023", date(2023, 10, 17)),
        ("Q3 2023", date(2023, 7, 1)),
        ("2024", date(2024, 1, 1)),
        ("", None),
        ("garbage", None),
    ],
)
def test_parse_date(text: str, expected: date | None) -> None:
    assert parse_date(text) == expected


def test_parse_int_picks_first_number() -> None:
    assert parse_int("16 cores") == 16
    assert parse_int("none") is None


def test_guess_cpu_segment_classifies_common_naming() -> None:
    assert guess_cpu_segment("Intel Xeon Platinum 8480") == "server"
    assert guess_cpu_segment("AMD EPYC 9755") == "server"
    assert guess_cpu_segment("AMD Ryzen Threadripper 7980X") == "hedt"
    assert guess_cpu_segment("Intel Core i7-13700K") == "desktop"
    assert guess_cpu_segment("Intel Core i7-13700H") == "laptop"
    assert guess_cpu_segment("AMD Ryzen 9 7945HX") == "laptop"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Intel Atom C3950", "server"),
        ("Intel Atom P5362", "server"),
        ("Intel Atom Z3570", "laptop"),
        ("Intel Atom x3-C3130", "laptop"),
        ("Intel Atom N270", "laptop"),
        ("Intel Atom D525", "desktop"),
        ("Intel Atom x6414RE", "desktop"),
    ],
)
def test_guess_cpu_segment_atom_lines(name: str, expected: str) -> None:
    assert guess_cpu_segment(name) == expected
