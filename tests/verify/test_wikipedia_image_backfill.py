"""Offline checks for conservative Commons image selection."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

from app.verify import wikipedia_image_backfill as backfill
from app.verify.wikipedia_image_backfill import (
    BAD_IMAGE,
    CATEGORIES,
    GROUP_IMAGE,
    NON_PHONE_MODEL,
    article_url,
    eligible,
    filename_matches_model,
    inspect,
    license_name,
    write_image,
)


def meta(short: str, *, artist: str = "A photographer", terms: str = "") -> dict:
    return {
        "LicenseShortName": {"value": short},
        "Artist": {"value": artist},
        "UsageTerms": {"value": terms},
    }


class FakeFetcher:
    def __init__(self, filename: str, metadata: dict) -> None:
        self.filename = filename
        self.metadata = metadata
        self.calls = 0

    def query(self, host: str, params: dict) -> dict:
        self.calls += 1
        if host.endswith("wikipedia.org"):
            return {
                "query": {
                    "pages": {
                        "1": {
                            "original": {
                                "source": "https://upload.wikimedia.org/wikipedia/commons/a/aa/"
                                + self.filename
                            }
                        }
                    }
                }
            }
        return {
            "query": {
                "pages": {
                    "1": {
                        "imageinfo": [
                            {
                                "url": "https://upload.wikimedia.org/wikipedia/commons/a/aa/"
                                + self.filename,
                                "mime": "image/jpeg",
                                "extmetadata": self.metadata,
                            }
                        ]
                    }
                }
            }
        }


def test_accepts_only_free_photo_with_attribution() -> None:
    fetcher = FakeFetcher("Example_phone.jpg", meta("CC-BY-SA-4.0"))
    result = inspect("https://en.wikipedia.org/wiki/Example_phone", fetcher)
    assert result["reason"] == "accepted"
    assert result["image_license"] == "CC-BY-SA-4.0"
    assert result["image_attribution"] == "A photographer"
    assert fetcher.calls == 2


@pytest.mark.parametrize("artist,credit", [
    ("A photographer", "Example Website"),
    ("Example", "Own work"),
])
def test_rejects_maker_marketing_shot_tagged_free(artist: str, credit: str) -> None:
    metadata = {**meta("CC0", artist=artist), "Credit": {"value": credit}}
    fetcher = FakeFetcher("Example_phone.jpg", metadata)
    result = inspect("https://en.wikipedia.org/wiki/Example_phone", fetcher, "Example Phone")
    assert result["reason"] == "bad_license"


def test_rejects_render_and_nonfree() -> None:
    render = FakeFetcher("Example_phone.png", meta("CC-BY-SA-4.0"))
    assert inspect("https://en.wikipedia.org/wiki/Example_phone", render)["reason"] == "logo_like"
    assert render.calls == 1
    nonfree = FakeFetcher("Example_phone.jpg", meta("Fair use"))
    assert (
        inspect("https://en.wikipedia.org/wiki/Example_phone", nonfree)["reason"] == "bad_license"
    )
    assert license_name(meta("CC-BY-SA-4.0", terms="Non-free media")) is None


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("missing_key", [False, True])
def test_category_scan_and_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, category: str, missing_key: bool
) -> None:
    record = {
        "name": "Example X123",
        "image_url": None,
        "source_urls": ["https://en.wikipedia.org/wiki/Example_X123"],
        "specification": {"untouched": True},
    }
    if missing_key:
        record.pop("image_url")
    for folder in CATEGORIES:
        directory = tmp_path / "data" / folder
        directory.mkdir(parents=True)
        (directory / "example.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    assert [
        path.parent.name
        for path, _, _ in eligible(tmp_path, category=category, include_missing_key=missing_key)
    ] == [category]
    monkeypatch.setattr(
        backfill, "CommonsFetcher", lambda _: FakeFetcher("Example_X123.jpg", meta("CC-BY-4.0"))
    )
    results = backfill.run(tmp_path, category=category, apply=True, include_missing_key=missing_key)
    assert [row["reason"] for row in results] == ["accepted"]
    for folder in CATEGORIES:
        updated = json.loads((tmp_path / "data" / folder / "example.json").read_text())
        assert updated["specification"] == record["specification"]
        assert (updated.get("image_url") is not None) == (folder == category)
    cache_path = tmp_path / "data" / "_verify" / "state" / "wikipedia_image_cache.jsonl"
    assert json.loads(cache_path.read_text())["path"] == f"data/{category}/example.json"


def test_malformed_records_skip_with_reason(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    directory = tmp_path / "data" / "laptop"
    directory.mkdir(parents=True)
    for name, record in {
        "array": [],
        "sources": {"image_url": None, "source_urls": 42},
    }.items():
        (directory / f"{name}.json").write_text(json.dumps(record), encoding="utf-8")
    (directory / "broken.json").write_text("{", encoding="utf-8")
    assert eligible(tmp_path, category="laptop") == []
    output = capsys.readouterr().out
    assert "record must be a JSON object" in output
    assert "source_urls must be a list" in output
    assert "unreadable record" in output
    assert article_url({"source_urls": 42}) is None
    with pytest.raises(ValueError, match="unsupported category"):
        eligible(tmp_path, category="../outside")


def test_apply_skips_incompatible_record_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "data" / "watch"
    directory.mkdir(parents=True)
    record = {
        "name": "Example X123",
        "image_url": None,
        "source_urls": ["https://en.wikipedia.org/wiki/Example_X123"],
    }
    incompatible = directory / "a.json"
    before = json.dumps(dict(record, image_license=None))
    incompatible.write_text(before, encoding="utf-8")
    (directory / "b.json").write_text(json.dumps(record), encoding="utf-8")
    (directory / "c.json").write_text(json.dumps(dict(record, name=None)), encoding="utf-8")
    monkeypatch.setattr(
        backfill, "CommonsFetcher", lambda _: FakeFetcher("Example_X123.jpg", meta("CC-BY-4.0"))
    )
    results = backfill.run(tmp_path, category="watch", apply=True)
    assert [row["reason"] for row in results] == ["invalid_record", "accepted", "invalid_record"]
    assert "existing image metadata" in results[0]["error"]
    assert "name must be a nonempty string" in results[2]["error"]
    assert incompatible.read_text() == before


@pytest.mark.parametrize("category", [None, "pda"])
def test_cli_selects_category(monkeypatch: pytest.MonkeyPatch, category: str | None) -> None:
    calls = []
    monkeypatch.setattr(backfill, "run", lambda *args, **kwargs: calls.append(kwargs) or [])
    argv = ["backfill", "--data-root", ".", "--apply"]
    if category:
        argv += ["--category", category]
    monkeypatch.setattr(sys, "argv", argv)
    backfill.main()
    assert calls[0]["category"] == (category or "smartphone")
    assert calls[0]["apply"] is True


def test_shared_cache_replays_without_crossing_categories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = FakeFetcher("Example_X123.jpg", meta("CC-BY-4.0"))
    monkeypatch.setattr(backfill, "CommonsFetcher", lambda _: fetcher)
    for category in ("laptop", "pda"):
        path = tmp_path / "data" / category / "example.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "name": "Example X123",
                    "image_url": None,
                    "source_urls": ["https://en.wikipedia.org/wiki/Example_X123"],
                }
            ),
            encoding="utf-8",
        )
        backfill.run(tmp_path, category=category)
    assert fetcher.calls == 4
    cache = tmp_path / "data" / "_verify" / "state" / "wikipedia_image_cache.jsonl"
    before = cache.read_bytes()
    backfill.run(tmp_path, category="laptop", apply=True)
    assert fetcher.calls == 4
    assert cache.read_bytes() == before
    assert json.loads((tmp_path / "data" / "laptop" / "example.json").read_text())["image_url"]
    assert json.loads((tmp_path / "data" / "pda" / "example.json").read_text())["image_url"] is None


def test_filename_must_name_device() -> None:
    assert filename_matches_model("HONOR Magic6 Pro", "Honor_Magic_6_Pro.jpg")
    assert not filename_matches_model("HONOR Magic5 Pro", "Honor_headquarter.jpg")
    assert not filename_matches_model("3X G750", "damaged_battery.jpg")
    assert not filename_matches_model("Galaxy A05", "Samsung_Galaxy_A05s_2024.jpg")
    assert not filename_matches_model("OnePlus 12R", "OnePlus_Ace_3_Black.jpg")
    assert filename_matches_model("HONOR Magic6 Pro", "Honor_Magic_6_Pro.jpg")
    fetcher = FakeFetcher("Honor_headquarter.jpg", meta("CC-BY-SA-4.0"))
    assert (
        inspect("https://en.wikipedia.org/wiki/Honor_Magic5_Pro", fetcher, "HONOR Magic5 Pro")[
            "reason"
        ]
        == "logo_like"
    )
    variant = FakeFetcher("OnePlus_7_Pro.jpg", meta("CC-BY-SA-4.0"))
    assert (
        inspect("https://en.wikipedia.org/wiki/OnePlus_7", variant, "OnePlus 7")["reason"]
        == "logo_like"
    )
    assert BAD_IMAGE.search("OnePlus_2_(in_packaging).jpg")
    assert BAD_IMAGE.search("Samsung_S26_시리즈.jpg")
    assert GROUP_IMAGE.search("Xiaomi_14T_Pro_and_Xiaomi_14T.jpg")
    assert NON_PHONE_MODEL.search("Surface 2")


def test_writes_only_image_fields() -> None:
    path = Path(tempfile.gettempdir()) / "wikipedia_image_backfill_test_phone.json"
    before = '{\n  "name": "Example",\n  "image_url": null,\n  "source_urls": ["https://en.wikipedia.org/wiki/Example"]\n}\n'
    path.write_text(before, encoding="utf-8")
    write_image(
        path,
        {
            "image_url": "https://upload.wikimedia.org/a.jpg",
            "image_license": "CC-BY-4.0",
            "image_attribution": "Alice",
        },
    )
    after = path.read_text(encoding="utf-8")
    assert json.loads(after)["source_urls"] == json.loads(before)["source_urls"]
    assert '  "name": "Example",\n' in after
    assert article_url(json.loads(after)) == "https://en.wikipedia.org/wiki/Example"
    path.unlink()


def test_eligible_requires_explicit_null_image_url() -> None:
    with tempfile.TemporaryDirectory(prefix="wiki_image_eligible_") as folder:
        root = Path(folder)
        phone_dir = root / "data" / "smartphone"
        phone_dir.mkdir(parents=True)
        source = ["https://en.wikipedia.org/wiki/Example"]
        (phone_dir / "missing.json").write_text(
            json.dumps({"source_urls": source}), encoding="utf-8"
        )
        (phone_dir / "null.json").write_text(
            json.dumps({"image_url": None, "source_urls": source}), encoding="utf-8"
        )
        assert [path.name for path, _, _ in eligible(root)] == ["null.json"]
        assert [path.name for path, _, _ in eligible(root, include_missing_key=True)] == [
            "missing.json",
            "null.json",
        ]


def test_write_image_inserts_missing_fields_without_other_changes(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    before = (
        '{\r\n  "name": "Example",\r\n  "source_urls": '
        '["https://en.wikipedia.org/wiki/Example"]\r\n}\r\n'
    )
    path.write_bytes(before.encode("utf-8"))
    write_image(
        path,
        {
            "image_url": "https://upload.wikimedia.org/a.jpg",
            "image_license": "CC-BY-4.0",
            "image_attribution": "Alice",
        },
    )
    after = path.read_bytes().decode("utf-8")
    assert after == before.replace(
        '  "name":',
        '  "image_url": "https://upload.wikimedia.org/a.jpg",\r\n'
        '  "image_license": "CC-BY-4.0",\r\n'
        '  "image_attribution": "Alice",\r\n'
        '  "name":',
        1,
    )
