"""Reusable CPU tables with stacked headers and rowspan family cells.

Keep CPU and GPU header paths separate, and apply units stated in headers
to bare values. A shared two-column Model header means family then SKU.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from bs4 import Tag

from .wikitable import _span


def _field(label: str) -> str | None:
    text = label.lower()
    if re.search(r"\b(gpu|npu)\b", text):
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
        return "l3_cache"
    if any(token in text for token in ("boost", "turbo", "pbo", "pb2")):
        return "boost_clock"
    if any(token in text for token in ("base", "clock", "freq")):
        return "base_clock"
    if "tdp" in text or "wattage" in text:
        return None if "configurable" in text else "tdp"
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


def parse_cpu_table(table: Tag) -> Iterator[dict[str, str]]:
    """Yield canonical fields, including ``family``, without vendor assumptions.

    Direct cell children avoid consuming nested rows from imperfect Wikipedia
    HTML. Row indices include empty rows so rowspan carry stays aligned.
    Full-width captions and repeated headers never become processors.
    """
    rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
    pending: dict[tuple[int, int], str] = {}
    grid: list[list[str]] = []
    header_flags: list[bool] = []
    captions: list[bool] = []
    for index, row in enumerate(rows):
        cells = row.find_all(["th", "td"], recursive=False)
        header_flags.append(bool(cells) and all(cell.name == "th" for cell in cells))
        captions.append(len(cells) == 1 and _span(cells[0], "colspan") > 1)
        values: list[str] = []
        column = 0
        for cell in cells:
            while (index, column) in pending:
                values.append(pending.pop((index, column)))
                column += 1
            for sup in cell.find_all("sup"):
                sup.decompose()
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
    # A colspan Model heading describes two distinct body columns.
    for column in range(len(fields) - 1):
        if fields[column : column + 2] == ["model", "model"]:
            fields[column] = "family"
    if "model" not in fields:
        return
    model_labels = {
        label.lower() for label, field in zip(labels, fields, strict=True) if field == "model"
    }
    for index in range(end, len(rows)):
        if captions[index]:
            continue
        result: dict[str, str] = {}
        for column, value in enumerate(grid[index][: len(fields)]):
            field = fields[column]
            if field and value:
                result.setdefault(field, _with_units(value, labels[column], field))
        if result.get("model") and result["model"].lower() not in model_labels:
            yield result
