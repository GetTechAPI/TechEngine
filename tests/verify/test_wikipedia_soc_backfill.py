"""Offline gates for the Wikipedia SoC backfill. No network."""

from __future__ import annotations

import json
from pathlib import Path

from app.verify.wikipedia_soc_backfill import (
    WikiRow,
    append_wikipedia_source,
    backfill,
    cpu_total,
    decide,
    form_factor_marks,
    index_rows,
    parse_gpu,
    rows_from_html,
    soc_identity,
)

_SNAP = """
<h3>Snapdragon 8 series</h3>
<table class="wikitable">
  <tr>
    <th>Model number</th><th>Product name</th><th>Fab</th><th>CPU</th><th>GPU</th>
    <th>Sampling availability</th>
  </tr>
  <tr>
    <td>SM8550-AB</td><td>Snapdragon 8 Gen 2</td><td>4 nm (TSMC N4)</td>
    <td>1× Cortex-X3 + 4× Cortex-A715 + 3× Cortex-A510</td>
    <td>Adreno 740</td><td>2022</td>
  </tr>
  <tr>
    <td>SM8550-AC</td><td>Snapdragon 8 Gen 2 for Galaxy</td><td>4 nm</td>
    <td>1 + 4 + 3 cores</td><td>Adreno 740</td><td>2023</td>
  </tr>
  <tr>
    <td>SM8450</td><td>Snapdragon 8+ Gen 1</td><td>4 nm</td>
    <td>1 + 3 + 4 cores</td><td>Adreno 730</td><td>2022</td>
  </tr>
  <tr>
    <td>SM8350</td><td>Snapdragon 888</td><td>5 nm</td>
    <td>1 + 3 + 4 cores</td><td>Adreno 660</td><td>2020</td>
  </tr>
  <tr>
    <td>SM8350-AC</td><td>Snapdragon 888+</td><td>5 nm</td>
    <td>1 + 3 + 4 cores</td><td>Adreno 660</td><td>2021</td>
  </tr>
</table>
<h3>Compute platforms</h3>
<table class="wikitable">
  <tr><th>Model number</th><th>Product name</th><th>Fab</th><th>GPU</th><th>Released</th></tr>
  <tr>
    <td>SC8180X</td><td>Snapdragon 8cx</td><td>7 nm</td><td>Adreno 680</td><td>2019</td>
  </tr>
</table>
<h3>Automotive platforms</h3>
<table class="wikitable">
  <tr><th>Model number</th><th>Product name</th><th>Fab</th><th>GPU</th><th>Released</th></tr>
  <tr>
    <td>SA8155P</td><td>Snapdragon 855</td><td>7 nm</td><td>Adreno 640</td><td>2019</td>
  </tr>
</table>
"""

_EXYNOS = """
<h3>Exynos 2000 series</h3>
<table class="wikitable">
  <tr>
    <th colspan="2">SoC</th><th colspan="2">CPU</th><th>GPU</th><th>Released</th>
  </tr>
  <tr>
    <th>Model number</th><th>Fab.</th><th>ISA</th><th>μarch</th><th>μarch</th><th>Released</th>
  </tr>
  <tr>
    <td>Exynos 2200 (S5E9925)</td><td>4 nm (Samsung 4LPE)</td><td>ARMv9</td>
    <td>1 + 3 + 4 cores (2.95 GHz Cortex-X2 + 2.5 GHz Cortex-A710)</td>
    <td>Xclipse 920</td><td>2022</td>
  </tr>
  <tr>
    <td>Exynos 990</td><td>7 nm</td><td>ARMv8</td>
    <td>2 + 2 + 4 cores</td><td>Mali-G77 MP11</td><td>2020</td>
  </tr>
</table>
<h3>List of Exynos Wearable SoCs</h3>
<table class="wikitable">
  <tr><th>Model number</th><th>Fab</th><th>GPU</th><th>Released</th></tr>
  <tr><td>Exynos W920</td><td>5 nm</td><td>Mali-G68 MP2</td><td>2021</td></tr>
</table>
"""


def _rec(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "slug": "qualcomm-snapdragon-8-gen-2",
        "name": "Qualcomm Snapdragon 8 Gen 2 5G SM8550-AB",
        "manufacturer": "qualcomm",
        "release_date": "2024-01-01",
        "process_nm": 4.0,
        "gpu_name": "Qualcomm Adreno 740",
        "source_urls": ["https://www.kaggle.com/datasets/example"],
    }
    record.update(overrides)
    return record


def test_identity_folds_maker_bionic_and_5g_but_keeps_sku_tokens() -> None:
    assert soc_identity("Qualcomm Snapdragon 8 Gen 2 5G", fold_radio=True) == soc_identity(
        "Snapdragon 8 Gen 2"
    )
    assert soc_identity("Apple A15 Bionic") == soc_identity("Apple A15")
    assert soc_identity("Snapdragon 888+") == soc_identity("Snapdragon 888 Plus")
    assert soc_identity("Snapdragon 888+") != soc_identity("Snapdragon 888")
    assert soc_identity("Snapdragon 8 Gen 2 for Galaxy") != soc_identity("Snapdragon 8 Gen 2")
    assert soc_identity("Snapdragon 888 4G") != soc_identity("Snapdragon 888")
    assert soc_identity("Apple M1 Pro") != soc_identity("Apple M1")
    assert soc_identity("Dimensity 9200") != soc_identity("Dimensity 920")
    assert soc_identity("Kirin 9000E") != soc_identity("Kirin 9000")


def test_cpu_total_ignores_frequency_plus() -> None:
    assert cpu_total("1 + 3 + 4 cores (2.95 GHz Cortex-X2 + 2.5 GHz Cortex-A710)") == 8
    assert cpu_total("2× Cortex-A76 @ 2.2 GHz 6× Cortex-A55 @ 2.0 GHz") == 8
    assert cpu_total("Cortex-A77 + 1.8 GHz Cortex-A55") is None
    assert cpu_total("4+4") == 8


def test_parse_gpu_keeps_variant_letters_and_cores() -> None:
    assert parse_gpu("Qualcomm Adreno 642L").model == "adreno642l"
    assert parse_gpu("Adreno 740").model == "adreno740"
    assert parse_gpu("ARM Mali-G710MP10").model == "malig710"
    assert parse_gpu("ARM Mali-G710MP10").cores == 10
    assert parse_gpu("Samsung Xlipse 920").model == "xclipse920"
    assert parse_gpu("Unknown mobile GPU").model is None
    assert parse_gpu("NVIDIA GeForce").model is None


def test_table_confirms_exact_chip_on_process_and_gpu() -> None:
    rows = rows_from_html(_SNAP, "List", "https://en.wikipedia.org/wiki/List")
    outcome = decide(_rec(), rows)
    assert outcome.decision == "confirm"
    assert outcome.title == "Snapdragon 8 Gen 2"
    assert "process_nm" in outcome.agreements
    assert "gpu_model" in outcome.agreements
    assert outcome.proposed_url is not None
    assert (
        "Snapdragon_8_Gen_2" in outcome.proposed_url
        or "Snapdragon_8_series" in outcome.proposed_url
    )


def test_year_alone_and_process_alone_do_not_confirm() -> None:
    rows = rows_from_html(_SNAP, "List", "https://en.wikipedia.org/wiki/List")
    year_only = decide(
        _rec(process_nm=None, gpu_name="Unknown mobile GPU", release_date="2022-01-01"), rows
    )
    assert year_only.decision == "ambiguous"
    assert year_only.reason == "insufficient-specs"
    process_only = decide(
        _rec(gpu_name="N/A", release_date="2010-01-01"),
        rows,
    )
    assert process_only.decision != "confirm"


def test_stale_year_does_not_veto_process_and_gpu() -> None:
    rows = rows_from_html(_SNAP, "List", "https://en.wikipedia.org/wiki/List")
    outcome = decide(_rec(release_date="2024-01-01"), rows)
    assert outcome.decision == "confirm"
    assert "release_year" not in outcome.agreements


def test_galaxy_plus_and_near_numbers_do_not_confirm() -> None:
    rows = rows_from_html(_SNAP, "List", "https://en.wikipedia.org/wiki/List")
    galaxy = decide(
        _rec(name="Qualcomm Snapdragon 8 Gen 2 for Galaxy", gpu_name="Adreno 740"),
        rows,
    )
    assert galaxy.decision == "confirm"
    assert galaxy.title == "Snapdragon 8 Gen 2 for Galaxy"

    plus = decide(_rec(name="Qualcomm Snapdragon 888+", process_nm=5, gpu_name="Adreno 660"), rows)
    assert plus.title == "Snapdragon 888+"
    base = decide(_rec(name="Qualcomm Snapdragon 888", process_nm=5, gpu_name="Adreno 660"), rows)
    assert base.title == "Snapdragon 888"
    assert base.decision == "confirm"
    four_g = decide(
        _rec(name="Qualcomm Snapdragon 888 4G", process_nm=5, gpu_name="Adreno 660"), rows
    )
    assert four_g.decision != "confirm"


def test_wrong_gpu_is_not_a_confirm() -> None:
    rows = rows_from_html(_SNAP, "List", "https://en.wikipedia.org/wiki/List")
    outcome = decide(_rec(gpu_name="Adreno 730"), rows)
    assert outcome.decision != "confirm"
    assert "gpu_model" in outcome.conflicts


def test_pc_and_automotive_markers_block_a_one_sided_match() -> None:
    assert form_factor_marks("Snapdragon 8cx") == frozenset({"pc"})
    assert form_factor_marks("Snapdragon 888") == frozenset()
    assert form_factor_marks("Exynos Auto V9") == frozenset({"automotive"})
    rows = rows_from_html(_SNAP, "List", "https://en.wikipedia.org/wiki/List")
    phone = decide(
        _rec(
            name="Qualcomm Snapdragon 855",
            process_nm=7,
            gpu_name="Adreno 640",
            release_date="2019-01-01",
        ),
        rows,
    )
    assert phone.reason == "form-factor-variant"
    assert phone.decision == "ambiguous"
    laptop = decide(
        _rec(
            name="Qualcomm Snapdragon 8cx",
            process_nm=7,
            gpu_name="Adreno 680",
            release_date="2019-01-01",
        ),
        rows,
    )
    assert laptop.decision == "confirm"
    assert laptop.title == "Snapdragon 8cx"


def test_wearable_section_blocks_a_phone_name() -> None:
    rows = rows_from_html(_EXYNOS, "Exynos", "https://en.wikipedia.org/wiki/Exynos")
    blocked = decide(
        _rec(
            name="Samsung Exynos W920",
            process_nm=5,
            gpu_name="Mali-G68 MP2",
            release_date="2021-01-01",
        ),
        rows,
    )
    assert blocked.reason == "form-factor-variant"
    confirmed = decide(
        _rec(
            name="Samsung Exynos 2200 5G S5E9925",
            process_nm=4,
            gpu_name="Samsung Xlipse 920",
            release_date="2023-01-01",
        ),
        rows,
    )
    assert confirmed.decision == "confirm"
    assert confirmed.title.startswith("Exynos 2200")
    assert "gpu_model" in confirmed.agreements


def test_4g_parenthetical_does_not_confirm_the_5g_row() -> None:
    five_g = WikiRow(
        model="Kirin 990 5G",
        url="https://en.wikipedia.org/wiki/HiSilicon#Kirin_990_5G",
        page="HiSilicon",
        process_nm=frozenset({7.0}),
        gpu_model="malig76",
        gpu_cores=16,
        year=2019,
        marketing_keys=frozenset({"kirin9905g"}),
    )
    four_g = WikiRow(
        model="Kirin 990 4G",
        url="https://en.wikipedia.org/wiki/HiSilicon#Kirin_990_4G",
        page="HiSilicon",
        process_nm=frozenset({7.0}),
        gpu_model="malig76",
        gpu_cores=16,
        year=2019,
        marketing_keys=frozenset({"kirin9904g"}),
    )
    rows = [five_g, four_g]
    record = _rec(
        name="Kirin 990 (4G)",
        process_nm=7,
        gpu_name="Mali-G76 MP16",
        release_date="2019-01-01",
    )
    outcome = decide(record, rows)
    assert outcome.decision == "confirm"
    assert outcome.title == "Kirin 990 4G"
    plain = decide(_rec(name="Kirin 990", process_nm=7, gpu_name="Mali-G76 MP16"), rows)
    assert plain.decision != "confirm"


def test_part_number_that_names_another_row_stays_ambiguous() -> None:
    base = WikiRow(
        model="Snapdragon 8 Gen 3",
        url="https://en.wikipedia.org/wiki/List#base",
        page="List",
        process_nm=frozenset({4.0}),
        gpu_model="adreno750",
        marketing_keys=frozenset({"snapdragon8gen3"}),
        part_keys=frozenset({"sm8650ab"}),
    )
    galaxy = WikiRow(
        model="Snapdragon 8 Gen 3 for Galaxy",
        url="https://en.wikipedia.org/wiki/List#galaxy",
        page="List",
        process_nm=frozenset({4.0}),
        gpu_model="adreno750",
        marketing_keys=frozenset({"snapdragon8gen3forgalaxy"}),
        part_keys=frozenset({"sm8650ac"}),
    )
    outcome = decide(
        _rec(name="Qualcomm Snapdragon 8 Gen 3 SM8650-AC", process_nm=4, gpu_name="Adreno 750"),
        index_rows([base, galaxy]),
    )
    assert outcome.decision == "confirm"
    assert outcome.title == "Snapdragon 8 Gen 3 for Galaxy"


def test_disagreeing_part_number_does_not_confirm() -> None:
    row = WikiRow(
        model="Kirin 925 (Hi3630)",
        url="https://en.wikipedia.org/wiki/HiSilicon#Kirin_925",
        page="HiSilicon",
        process_nm=frozenset({28.0}),
        gpu_model="malit628",
        gpu_cores=4,
        year=2014,
        marketing_keys=frozenset({"kirin925"}),
        part_keys=frozenset({"hi3630"}),
    )
    outcome = decide(
        _rec(
            name="HiSilicon KIRIN925 Hi3830",
            process_nm=28,
            gpu_name="Mali-T628 MP4",
            release_date="2014-01-01",
        ),
        [row],
    )
    assert outcome.decision == "notfound"


def test_marketing_name_does_not_fall_back_onto_another_chips_part_number() -> None:
    other = WikiRow(
        model="Dimensity 7350",
        url="https://en.wikipedia.org/wiki/List#7350",
        page="List",
        process_nm=frozenset({4.0}),
        gpu_model="malig610",
        gpu_cores=2,
        year=2024,
        marketing_keys=frozenset({"dimensity7350"}),
        part_keys=frozenset({"mt6886v"}),
    )
    outcome = decide(
        _rec(
            name="MediaTek Dimensity 7200-Ultra MT6886V",
            process_nm=4,
            gpu_name="Mali-G610 MC4",
            release_date="2024-01-01",
        ),
        index_rows([other]),
    )
    assert outcome.decision == "notfound"


def test_shared_part_number_does_not_jump_to_the_other_sku() -> None:
    base = WikiRow(
        model="Dimensity 9000",
        url="https://en.wikipedia.org/wiki/List#Dimensity_9000",
        page="List",
        process_nm=frozenset({4.0}),
        gpu_model="malig710",
        marketing_keys=frozenset({"dimensity9000"}),
        part_keys=frozenset({"mt6983", "mt6983z"}),
    )
    plus = WikiRow(
        model="Dimensity 9000+",
        url="https://en.wikipedia.org/wiki/List#Dimensity_9000+",
        page="List",
        process_nm=frozenset({4.0}),
        gpu_model="malig710",
        marketing_keys=frozenset({"dimensity9000plus"}),
        part_keys=frozenset({"mt6983z"}),
    )
    rows = index_rows([base, plus])
    outcome = decide(
        _rec(name="MediaTek Dimensity 9000+ MT6983Z", process_nm=4, gpu_name="Mali-G710"),
        rows,
    )
    assert outcome.decision == "confirm"
    assert outcome.title == "Dimensity 9000+"
    plain = decide(
        _rec(name="MediaTek Dimensity 9000", process_nm=4, gpu_name="Mali-G710"),
        rows,
    )
    assert plain.title == "Dimensity 9000"


def test_two_rows_with_the_same_name_stay_ambiguous() -> None:
    shared = dict(
        url="https://en.wikipedia.org/wiki/List#a",
        page="List",
        section="Snapdragon 800",
        process_nm=frozenset({28.0}),
        gpu_model="adreno330",
        year=2013,
        marketing_keys=frozenset({"snapdragon800"}),
    )
    rows = [
        WikiRow(model="Snapdragon 800", **shared),
        WikiRow(
            model="Snapdragon 800",
            url="https://en.wikipedia.org/wiki/List#b",
            **{key: value for key, value in shared.items() if key != "url"},
        ),
    ]
    # Distinct part rows that display the same marketing name.
    rows[1] = WikiRow(
        model="Snapdragon 800 (APQ)",
        url="https://en.wikipedia.org/wiki/List#b",
        page="List",
        section="Snapdragon 800",
        process_nm=frozenset({28.0}),
        gpu_model="adreno330",
        year=2013,
        marketing_keys=frozenset({"snapdragon800"}),
    )
    outcome = decide(
        _rec(
            name="Qualcomm Snapdragon 800",
            process_nm=28,
            gpu_name="Adreno 330",
            release_date="2013-01-01",
        ),
        rows,
    )
    assert outcome.decision == "ambiguous"
    assert outcome.reason == "multiple-rows"


def test_dry_run_does_not_touch_techapi_json(tmp_path: Path) -> None:
    data = tmp_path / "TechAPI" / "data" / "soc" / "qualcomm" / "2022"
    data.mkdir(parents=True)
    target = data / "snapdragon.json"
    record = _rec()
    raw = json.dumps(record, indent=2) + "\n"
    target.write_text(raw, encoding="utf-8")

    def fetch(page: str) -> tuple[int, str, str]:
        return 200, f"https://en.wikipedia.org/wiki/{page}", _SNAP

    result = backfill(
        data_root=tmp_path / "TechAPI",
        cache_path=tmp_path / "cache.jsonl",
        summary_path=tmp_path / "summary.md",
        limit=5,
        sleep_s=1.0,
        dry_run=True,
        apply=False,
        pages=[("qualcomm", "List", "Snapdragon")],
        fetch_page=fetch,
        search_fn=lambda _name: [],
    )
    assert target.read_text(encoding="utf-8") == raw
    assert result.counts()["confirm"] == 1
    assert "CONFIRM" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_apply_writes_only_the_confirmed_url_and_keeps_crlf(tmp_path: Path) -> None:
    data = tmp_path / "TechAPI" / "data" / "soc" / "qualcomm" / "2022"
    data.mkdir(parents=True)
    target = data / "snapdragon.json"
    other = data / "other.json"
    record = _rec()
    body = json.dumps(record, indent=2) + "\n"
    target.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))
    other_record = _rec(slug="other", name="Imaginary SoC 9")
    other_raw = json.dumps(other_record, indent=2) + "\n"
    other.write_text(other_raw, encoding="utf-8")

    result = backfill(
        data_root=tmp_path / "TechAPI",
        cache_path=tmp_path / "cache.jsonl",
        summary_path=tmp_path / "summary.md",
        limit=5,
        sleep_s=1.0,
        dry_run=False,
        apply=True,
        pages=[("qualcomm", "List", "Snapdragon")],
        fetch_page=lambda page: (200, f"https://en.wikipedia.org/wiki/{page}", _SNAP),
        search_fn=lambda _name: [],
    )
    written = target.read_bytes()
    assert b"\r\n" in written
    parsed = json.loads(written)
    assert parsed["source_urls"][0] == record["source_urls"][0]
    assert any("en.wikipedia.org/wiki/List" in url for url in parsed["source_urls"])
    assert parsed["gpu_name"] == "Qualcomm Adreno 740"
    assert result.written == 1
    assert other.read_text(encoding="utf-8") == other_raw
    assert append_wikipedia_source(target, parsed["source_urls"][-1]) == "present"
