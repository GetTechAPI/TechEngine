"""Offline checks for conservative Commons image selection."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.verify.wikipedia_image_backfill import (
    BAD_IMAGE,
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


def test_rejects_render_and_nonfree() -> None:
    render = FakeFetcher("Example_phone.png", meta("CC-BY-SA-4.0"))
    assert inspect("https://en.wikipedia.org/wiki/Example_phone", render)["reason"] == "logo_like"
    assert render.calls == 1
    nonfree = FakeFetcher("Example_phone.jpg", meta("Fair use"))
    assert (
        inspect("https://en.wikipedia.org/wiki/Example_phone", nonfree)["reason"] == "bad_license"
    )
    assert license_name(meta("CC-BY-SA-4.0", terms="Non-free media")) is None


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
