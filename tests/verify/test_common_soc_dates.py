"""Use a source chip year when a normalized SoC date is a later placeholder."""

from app.verify import offline
from app.verify.common import Record, foreign_key_sets


def _release_for(release_date: str, raw_chipset: str | None) -> str:
    soc = Record("soc", "soc/chip-x.json", {
        "slug": "chip-x", "release_date": release_date, "raw_chipset": raw_chipset,
    })
    return foreign_key_sets({"soc": [soc]})[2]["chip-x"]


def test_earlier_structured_chip_year_corrects_late_placeholder():
    assert _release_for(
        "2025-01-01", "Qualcomm Snapdragon 680 SM6225, 2021, 64 bit, octa-core"
    ) == "2021-01-01"

    phone = Record("smartphone", "smartphone/phone-x.json", {
        "slug": "phone-x", "soc": "chip-x", "release_date": "2022-08-01",
        "source_urls": ["https://www.qualcomm.com/products/mobile/snapdragon"],
    })
    before = offline.score_record(phone, 2026, {"chip-x": "2025-01-01"})
    after = offline.score_record(phone, 2026, {"chip-x": "2021-01-01"})
    assert "soc_not_after_device" in before.flags
    assert "soc_not_after_device" not in after.flags


def test_precise_or_unstructured_dates_keep_the_normalized_date():
    assert _release_for("2024-05-07", "Chip X, 2021, 8 cores") == "2024-05-07"
    assert _release_for("2025-01-01", "Chip X with 2021 revision") == "2025-01-01"
    assert _release_for("2025-01-01", None) == "2025-01-01"
    assert _release_for("2025-01-01", "Chip X, 2026, 8 cores") == "2025-01-01"


def test_raw_chip_year_still_flags_a_genuine_era_mismatch():
    release = _release_for(
        "2024-01-01", "Qualcomm Snapdragon 8s Gen 3 SM8635, 2024, 8 cores"
    )
    phone = Record("smartphone", "smartphone/phone-x.json", {
        "slug": "phone-x", "soc": "chip-x", "release_date": "2015-01-09",
    })
    score = offline.score_record(phone, 2026, {"chip-x": release})
    assert "soc_not_after_device" in score.flags
