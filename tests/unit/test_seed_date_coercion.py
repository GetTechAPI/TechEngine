"""The seed loader must turn ISO date strings into date objects.

Regression test for a category whose date column is not named ``release_date``:
websites use ``launch_date``, and while the field-name-specific coercion was in
place such a record reached SQLite as a str and the insert failed with
"SQLite Date type only accepts Python date objects as input".

The endpoint tests insert fixtures through the model with real date objects, so
they never covered the JSON -> DB path this exercises.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.seed import _load_dir


def test_load_dir_coerces_any_date_suffixed_field(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(
        json.dumps(
            {
                "slug": "example-site",
                "name": "Example",
                "launch_date": "2001-01-15",
                "release_date": "1998-01-02",
                "verified": False,
                "source_urls": ["https://example.com"],
            }
        ),
        encoding="utf-8",
    )

    records = _load_dir(tmp_path)

    assert len(records) == 1
    assert records[0]["launch_date"] == date(2001, 1, 15)
    assert records[0]["release_date"] == date(1998, 1, 2)


def test_load_dir_leaves_non_date_fields_alone(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(
        json.dumps({"slug": "x", "name": "X", "homepage_url": "https://x.example"}),
        encoding="utf-8",
    )

    records = _load_dir(tmp_path)

    assert records[0]["homepage_url"] == "https://x.example"


def test_load_dir_missing_directory_is_empty(tmp_path: Path) -> None:
    assert _load_dir(tmp_path / "nope") == []
