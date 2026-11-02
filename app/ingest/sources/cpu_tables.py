"""Reusable CPU tables with stacked headers and rowspan family cells.

Keep CPU and GPU header paths separate, and apply units stated in headers
to bare values. A shared two-column Model header means family then SKU.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from bs4 import Tag

from ..normalize import parse_frequency_ghz
from .wikitable import _span


def _field(label: str) -> str | None:
    text = label.lower()
    # Integrated GPU/NPU columns ("Arc Graphics / X Cores") describe another chip,
    # and "Memory / Clock (MT/s)" the memory bus.
    if re.search(r"\b(i?gpu|igp|npu|graphics|memory)\b", text):
        return None
    if "branding" in text or "family" in text:
        return "family" if "model" not in text else "model"
    leaf = text.split(" / ")[-1].strip()
    if "model" in text or leaf in {"processor", "processor number", "name", "cpu"}:
        return "model"
    if any(token in text for token in ("architecture", "codename", "code name", "core name")):
        return "architecture"
    if "cores" in text:
        return "cores"
    if "threads" in text:
        return "threads"
    if "l3" in text or "smart cache" in text:
        # "L3 per CCX" is one chiplet's share, not the chip's L3.
        return None if re.search(r"\bper\b", text) else "l3_cache"
    # Before the clock checks: "TDP / Base" and "Power / Turbo" are watts.
    if "tdp" in text or "wattage" in text or text.startswith("power"):
        # Only the base (default) figure is tdp_w. cTDP up/down, turbo (PL2) and
        # any other sub-label ("TDP (W) / E" from a misaligned header) are not.
        parts = [part.strip() for part in text.split(" / ") if part.strip()]
        if (
            "configurable" in text
            or parts[0].startswith("ctdp")
            or any(not part.startswith(("base", "default", "nominal")) for part in parts[1:])
        ):
            return None
        return "tdp"
    # Clocks of the efficiency cores, a cTDP-down base and a minimum base are not
    # the chip's base or boost clock.
    if leaf in {"e", "lp-e"}:
        return None
    if any(token in text for token in ("boost", "turbo", "pbo", "pb2")):
        return "boost_clock"
    if any(token in text for token in ("base", "clock", "freq")):
        return None if re.search(r"\b(min|down)\b", leaf) else "base_clock"
    if "release" in text or "launch" in text:
        return None if any(token in text for token in ("price", "msrp")) else "release_date"
    if "socket" in text:
        return "socket"
    if any(token in text for token in ("process", "fab", "lithography")):
        return "process_node"
    return None


def _with_units(value: str, label: str, field: str) -> str:
    if not re.search(r"\d", value):
        return value
    if field == "cores":
        pair = re.match(r"\s*(\d+)\s*(?:/\s*(\d+)|\(\s*(\d+)\s*\))", value)
        count = re.match(r"\s*(\d+)\b", value)
        if "thread" in label.lower() and pair:
            return f"{pair[1]} / {pair[2] or pair[3]}"
        return count[1] if count else ""
    units = {
        "base_clock": r"\b(MHz|GHz)\b",
        "boost_clock": r"\b(MHz|GHz)\b",
        "l3_cache": r"\b(KiB|MiB|GiB|KB|MB|GB)\b",
        "tdp": r"\b(W)\b",
    }
    if field in ("base_clock", "boost_clock"):
        value = _clock_value(value, field)
    pattern = units.get(field)
    if pattern and not re.search(pattern, value, re.I):
        match = re.search(pattern, label, re.I)
        if match:
            value += " " + match.group(1)
    # Existing cache normalization expects KB/MB/GB; Wikipedia also uses IEC units.
    if field == "l3_cache":
        value = re.sub(r"\b([KMG])iB\b", r"\1B", value, flags=re.I)
        product = re.fullmatch(r"\s*(\d+)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(MB|KB|GB)\s*", value, re.I)
        if product:
            value = f"{int(product[1]) * float(product[2])} {product[3]}"
    return value


# "5.0 / 3.7": Zen 4 and Zen 4c clocks in one cell. "4.0 (3.7)": PBO with 1–2
# cores and with more. Both decimals are required: a misaligned table puts core
# counts such as "4 (4)" under clock headers.
_TWO_CLOCKS_RE = re.compile(r"\s*(\d+\.\d+)\s*/\s*\d+(?:\.\d+)?\s*")
_PBO_CLOCKS_RE = re.compile(r"\s*(\d+\.\d+)\s*\(\s*\d+\.\d+\s*\)\s*")


def _clock_value(value: str, field: str) -> str:
    """The clock a cell states for ``field``, or ``""`` when it states none.

    The first value of ``"5.0 / 3.7"`` is the max boost, but the cell names no
    single base clock. PBO ``"4.0 (3.7)"`` boosts to the first value.
    """
    if (two := _TWO_CLOCKS_RE.fullmatch(value)) is not None:
        return two[1] if field == "boost_clock" else ""
    if (pbo := _PBO_CLOCKS_RE.fullmatch(value)) is not None:
        return pbo[1]
    return value


def _clock_ghz(value: str) -> float:
    """Sort key for boost columns; a misread cell ("Q4 2017 GHz") sorts last."""
    ghz = parse_frequency_ghz(value)
    return ghz if ghz is not None and 0.1 <= ghz <= 7 else -1.0


_HYBRID_PARTS = {"p", "e", "lp-e", "total"}
_COUNT_RE = re.compile(r"\s*(\d+)\s*(?:\(\s*(\d+)\s*\)|/\s*(\d+))?")


def _count(value: str) -> tuple[int, int | None]:
    """``"2 (4)"`` → ``(2, 4)``; ``"8"`` → ``(8, None)``; ``"—"`` → ``(0, 0)``."""
    match = _COUNT_RE.match(value)
    if match is None:
        return 0, 0
    threads = match[2] or match[3]
    return int(match[1]), int(threads) if threads else None


def _hybrid_cores(parts: dict[str, str]) -> tuple[str, str]:
    """Total ``"cores / threads"`` of a hybrid chip and a note when it is unsure.

    Hybrid Intel tables split "Cores (threads)" into P, E, LP-E and Total
    columns. The total is the sum of the parts; when the page's Total cell
    disagrees with that sum, neither can be trusted and the count is left out.
    """
    counted = [_count(value) for leaf, value in parts.items() if leaf != "total"]
    cores = sum(c for c, _ in counted) if counted else None
    threads = (
        sum(t for _, t in counted if t is not None)
        if counted and all(t is not None for _, t in counted)
        else None
    )
    if "total" in parts:
        total_cores, total_threads = _count(parts["total"])
        if cores is not None and (
            cores != total_cores
            or (threads is not None and total_threads is not None and threads != total_threads)
        ):
            note = (
                f"P/E/LP-E cores add up to {cores} ({threads}) "
                f"but the Total column says {parts['total']}"
            )
            return "", note
        cores = total_cores
        threads = total_threads if total_threads is not None else threads
    if not cores:
        return "", ""
    return (f"{cores} / {threads}" if threads else str(cores)), ""


def parse_cpu_table(table: Tag) -> Iterator[dict[str, str]]:
    """Yield canonical fields, including ``family``, without vendor assumptions.

    Direct cell children avoid consuming nested rows from imperfect Wikipedia
    HTML. Row indices include empty rows so rowspan carry stays aligned, but
    not the hidden ``mw-empty-elt`` rows browsers never lay out. Full-width
    captions and repeated headers never become processors. Boost is the highest
    boost column (all-core and Turbo 2.0 columns come before the max turbo);
    every other field takes its first non-empty column.
    """
    rows = [
        row
        for row in table.find_all("tr")
        if row.find_parent("table") is table and "mw-empty-elt" not in (row.get("class") or [])
    ]
    pending: dict[tuple[int, int], str] = {}
    grid: list[list[str]] = []
    header_flags: list[bool] = []
    lone_spans: list[int] = []
    for index, row in enumerate(rows):
        cells = row.find_all(["th", "td"], recursive=False)
        header_flags.append(bool(cells) and all(cell.name == "th" for cell in cells))
        lone_spans.append(_span(cells[0], "colspan") if len(cells) == 1 else 0)
        values: list[str] = []
        column = 0
        for cell in cells:
            while (index, column) in pending:
                values.append(pending.pop((index, column)))
                column += 1
            for sup in cell.find_all("sup"):
                sup.decompose()
            # {{0}} pads numbers with a hidden "0" ("065 W" shows as " 65 W").
            for pad in cell.select('[aria-hidden="true"]'):
                if "visibility:hidden" in str(pad.get("style", "")).replace(" ", ""):
                    pad.decompose()
            text = re.sub(r"\[[^\]]*\]", "", cell.get_text(" ", strip=True)).strip()
            width = min(_span(cell, "colspan"), 64 - column)
            for offset in range(width):
                values.append(text)
                for extra in range(1, min(_span(cell, "rowspan"), len(rows) - index)):
                    pending[index + extra, column + offset] = text
            column += width
            if column >= 64:
                break
        while (index, column) in pending and column < 64:
            values.append(pending.pop((index, column)))
            column += 1
        grid.append(values)

    # A lone cell is a caption only when it spans the table: "2.0" under a
    # "Turbo Boost" heading is a header cell.
    width = max(map(len, grid), default=0)
    captions = [span > 1 and span >= width for span in lone_spans]
    start = 0
    while start < len(rows) and (not grid[start] or captions[start]):
        start += 1
    end = start
    while end < len(rows) and header_flags[end] and not captions[end]:
        end += 1
    if end == start:
        return
    labels = []
    for column in range(max(map(len, grid[start:end]))):
        parts = list(dict.fromkeys(row[column] for row in grid[start:end] if column < len(row)))
        labels.append(" / ".join(parts))
    fields = [_field(label) for label in labels]
    # "Base / Min" and "Base / Max" pair with "TDP / Min" and "TDP / Max", and
    # either one can be the nominal TDP's base (11980HK: Min, 11800H: Max).
    leaves = {label.lower().split(" / ")[-1].strip() for label in labels if "base" in label.lower()}
    if {"min", "max"} <= leaves:
        fields = [None if field == "base_clock" else field for field in fields]
    # A colspan Model heading describes two distinct body columns.
    for column in range(len(fields) - 1):
        if fields[column : column + 2] == ["model", "model"]:
            fields[column] = "family"
    if "model" not in fields:
        return
    model_labels = {
        label.lower() for label, field in zip(labels, fields, strict=True) if field == "model"
    }
    hybrid = {
        column: leaf
        for column, (label, field) in enumerate(zip(labels, fields, strict=True))
        if field == "cores" and (leaf := label.split(" / ")[-1].strip().lower()) in _HYBRID_PARTS
    }
    for index in range(end, len(rows)):
        if captions[index]:
            continue
        result: dict[str, str] = {}
        boosts: list[str] = []
        for column, value in enumerate(grid[index][: len(fields)]):
            field = fields[column]
            if field and value:
                value = _with_units(value, labels[column], field)
                result.setdefault(field, value)
                if field == "boost_clock":
                    boosts.append(value)
        if len(boosts) > 1:
            result["boost_clock"] = max(boosts, key=_clock_ghz)
        if hybrid:
            split = {
                leaf: grid[index][column]
                for column, leaf in hybrid.items()
                if column < len(grid[index]) and grid[index][column]
            }
            result["cores"], note = _hybrid_cores(split)
            if note:
                result["cores_note"] = note
        if result.get("model") and result["model"].lower() not in model_labels:
            yield result
