from typing import Any

from app.verify.catalog_candidates import Candidate, Index, gate
from app.verify.common import Record


def _rec(path: str, **data: Any) -> Record:
    return Record(path.split("/")[0], path, data)


INDEX = Index(
    [
        _rec("smartphone/realme/2021/q3-pro-carnival/a.json", brand="realme", slug="a",
             base_model_slug="q3-pro-carnival", variant={"model_numbers": ["RMX3142"]}),
        _rec("smartphone/asus/2015/zenfone-2-laser-ze550kl/b.json", brand="asus",
             slug="zenfone-2-laser-ze550kl"),
        _rec("smartphone/fairphone/2023/5/c.json", brand="fairphone", slug="fairphone-5",
             model_numbers=["FP5"]),
        _rec("smartphone/lenovo/2013/yoga-tablet-10.json", brand="lenovo", slug="yoga-tablet-10"),
    ],
    ["realme", "asus", "fairphone", "lenovo", "alcatel", "tcl", "kogan", "foxconn", "acme",
     "hisense", "blackview"],
)


def cand(brand: str, name: str, models: list[str] | None = None, **kw: Any) -> Candidate:
    return Candidate(brand=brand, name=name, models=models or [name],
                     codenames=kw.pop("codenames", []), **kw)


def decide(*cs: Candidate, rows: list[Candidate] | None = None) -> list[list[str]]:
    return [d.reasons for d in gate(list(cs), rows or list(cs), INDEX)]


def test_new_white_label_phone_is_accepted() -> None:
    assert decide(cand("acme", "Acme X9 Pro", ["X9PRO"], form_factor="Phone")) == [[]]


def test_dup_by_variant_model_number_any_brand() -> None:
    assert decide(cand("realme", "realme Q3 Pro Play", ["RMX3142"])) == [["dup:id"]]


def test_dup_by_region_suffixed_id_and_name_in_slug() -> None:
    assert decide(cand("fairphone", "Fairphone 5 5G", ["FP5_EEA"]))[0][0] == "dup:region_id"
    assert decide(cand("asus", "ZenFone 2 Laser (ZE550KL)", ["Z00LD"]))[0] == ["dup:name_in_slug"]


def test_old_major_rows_are_held() -> None:
    assert "dup:old_major" in decide(cand("lenovo", "VIBE K6 Note", ["K53a48"], sdk_min=23))[0]


def test_junk_signals() -> None:
    assert decide(cand("acme", "Acme 65 4K TV", ["T65"]))[0] == ["junk:name"]
    board = cand("acme", "Board", ["B1"], soc_raw="Rockchip RK3588")
    assert decide(board)[0][0] == "junk:soc_or_screen"


def test_vendor_mix_holds_rows_without_console() -> None:
    tvs = [cand("hisense", f"H{i}", [f"H{i}00"], form_factor="TV") for i in range(9)]
    row = cand("hisense", "Hisense E22", ["HE22"])
    assert decide(row, rows=[*tvs, row]) == [["junk:vendor_mix"]]


def test_odm_brand_held_by_share_of_foreign_names() -> None:
    rows = [cand("foxconn", "Kogan Agora 8", ["KA8"]), cand("foxconn", "F1", ["F100"])]
    assert decide(*rows)[1] == ["seller:odm_vendor"]
    rows = [cand("bigodm", "Kogan Agora 8", ["KA8"]), cand("bigodm", "Q1", ["Q100"])]
    assert decide(*rows)[1] == ["seller:odm_share"]


def test_sibling_brand_named_in_label_is_refiled() -> None:
    (d,) = gate([cand("alcatel", "TCL 30E", ["6127A"])], [], INDEX)
    assert d.candidate.brand == "tcl" and d.candidate.extra["refiled_from"] == "alcatel"


def test_market_suffix_label_takes_clean_name() -> None:
    (d,) = gate([cand("blackview", "WP33_Pro_EEA", ["WP33 Pro"], form_factor="Phone")], [], INDEX)
    assert d.candidate.name == "WP33 Pro"


def test_batch_dedupe_keeps_first() -> None:
    a, b = cand("acme", "Acme G3", ["V2443A"]), cand("acme", "Acme Y50i", ["V2443A"])
    assert decide(a, b) == [[], ["batch:same_model"]]
