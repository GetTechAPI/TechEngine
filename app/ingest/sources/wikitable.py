"""Wikipedia ``table.wikitable`` → grid of cell strings.

Wikipedia list pages use ``rowspan``/``colspan`` heavily (e.g. an
``Architecture`` cell spans every SKU in a generation). A row-by-row read
of ``<td>``s yields ghost-empty cells where a spanning cell *should*
appear. This module materialises a real 2-D grid that respects spans,
plus a header-keyword matcher that maps columns to canonical field names.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from bs4 import Tag

_MAX_COLS = 64  # paranoid bound; real CPU/GPU tables have ≤ ~20 columns
_HEADER_TAGS = ("th", "td")


@dataclass(frozen=True)
class GridRow:
    """One materialised table row keyed by canonical field name."""

    cells: dict[str, str]
    is_header: bool


def parse_table(table: Tag, header_rules: dict[str, list[str]]) -> Iterator[GridRow]:
    """Yield rows of ``table`` mapped to canonical fields per ``header_rules``.

    ``header_rules`` maps each canonical field name to a list of lowercased
    header-text fragments. The first header row encountered (with at least
    one recognized fragment) sets the column → field mapping for the rest of
    the table. Empty cells are skipped from the per-row dict.
    """
    grid = _table_to_grid(table)
    if not grid:
        return
    headers = _detect_headers(grid, header_rules)
    if not headers:
        return
    header_row_idx = headers["__row_index__"]
    column_to_field = {
        col: field for col, field in headers.items() if isinstance(col, int)
    }
    for row_idx, row in enumerate(grid):
        if row_idx <= header_row_idx:
            continue
        cells: dict[str, str] = {}
        for col_idx, text in enumerate(row):
            field = column_to_field.get(col_idx)
            if field is None or not text:
                continue
            cells.setdefault(field, text)
        if cells:
            yield GridRow(cells=cells, is_header=False)


def _table_to_grid(table: Tag) -> list[list[str]]:
    rows = table.select("tr")
    if not rows:
        return []
    grid: list[list[str]] = []
    pending: dict[tuple[int, int], str] = {}
    for row_idx, row in enumerate(rows):
        cells = [c for c in row.find_all(_HEADER_TAGS) if isinstance(c, Tag)]
        col_idx = 0
        cell_idx = 0
        out: list[str] = []
        while col_idx < _MAX_COLS:
            if (row_idx, col_idx) in pending:
                out.append(pending.pop((row_idx, col_idx)))
                col_idx += 1
                continue
            if cell_idx >= len(cells):
                break
            cell = cells[cell_idx]
            text = cell.get_text(" ", strip=True)
            colspan = _span(cell, "colspan")
            rowspan = _span(cell, "rowspan")
            for offset in range(colspan):
                if col_idx + offset >= len(out):
                    out.append(text)
                else:
                    out[col_idx + offset] = text
            for r in range(1, rowspan):
                for offset in range(colspan):
                    pending[(row_idx + r, col_idx + offset)] = text
            col_idx += colspan
            cell_idx += 1
        if out:
            grid.append(out)
    return grid


def _detect_headers(
    grid: list[list[str]], header_rules: dict[str, list[str]]
) -> dict[object, object]:
    """Return ``{col_idx: field_name, "__row_index__": row_idx}`` or ``{}``."""
    for row_idx, row in enumerate(grid):
        mapping: dict[int, str] = {}
        for col_idx, text in enumerate(row):
            field = _match_header(text.lower(), header_rules)
            if field is not None:
                mapping.setdefault(col_idx, field)
        if mapping and "model" in mapping.values():
            return {**mapping, "__row_index__": row_idx}
    return {}


def _match_header(text: str, rules: dict[str, list[str]]) -> str | None:
    for canonical, fragments in rules.items():
        for fragment in fragments:
            if fragment in text:
                return canonical
    return None


def _span(cell: Tag, attribute: str) -> int:
    raw = cell.attrs.get(attribute)
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if raw is None:
        return 1
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1
