"""image_url / logo_url may only link Wikimedia Commons files (ADR-017)."""

from __future__ import annotations

from typing import Any

import pytest

from app import validate

PHOTO = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Example_phone.jpg"
CREDIT = {"image_license": "CC-BY-SA-4.0", "image_attribution": "Example Author"}


@pytest.mark.parametrize("record,needle", [
    ({}, None),
    ({"image_url": None, "logo_url": None}, None),
    ({"image_url": PHOTO, **CREDIT}, None),
    ({"image_url": PHOTO}, "needs image_license"),
    ({"image_url": PHOTO, "image_license": "CC-BY-SA-4.0"}, "needs image_license"),
    ({"image_url": "https://cdn2.gsmarena.com/vv/bigpic/x.jpg"}, "must be an upload"),
    ({"image_url": "https://aitoolbuzz.com/assets/agents/mob/x.jpg", **CREDIT},
     "must be an upload"),
    ({"image_url": "https://upload.wikimedia.org/wikipedia/en/a/ab/Non_free.jpg", **CREDIT},
     "must be an upload"),
    ({"image_url": None, "raw_merged_records": [{"image_url": "http://www.mobiledokan.com/x.jpg"}]},
     "must be an upload"),
    ({"image_url": PHOTO, **CREDIT, "raw_merged_records": [{"image_url": PHOTO}]}, None),
    ({"logo_url": "https://commons.wikimedia.org/wiki/Special:FilePath/Acme.svg"}, None),
    ({"logo_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/30/A.svg/330px-A.svg.png"},
     None),
    ({"logo_url": "https://cdn.shopify.com/s/files/logo.png"}, "logo_url must be"),
])
def test_media_links(record: dict[str, Any], needle: str | None) -> None:
    errors: list[str] = []
    validate._check_media("x.json", record, errors)
    assert (errors == []) if needle is None else any(needle in e for e in errors), errors
