"""Item check: a record against the Wikidata item it cites (offline)."""

from typing import Any

from app.verify import itemcheck, promote
from app.verify.itemcheck import AGREE, CONFIRM, CONFLICT, CONTRADICT, INSUFFICIENT, NA


def item(value: str) -> dict[str, Any]:
    return {"mainsnak": {"datavalue": {"value": {"id": value}}}, "rank": "normal"}


def text(value: str, rank: str = "normal") -> dict[str, Any]:
    return {"mainsnak": {"datavalue": {"value": value}}, "rank": rank}


def when(time: str, precision: int) -> dict[str, Any]:
    return {"mainsnak": {"datavalue": {"value": {"time": time, "precision": precision}}},
            "rank": "normal"}


def entity(label: str, aliases: tuple[str, ...] = (), **claims: list[dict[str, Any]]):
    return {
        "labels": {"en": {"value": label}},
        "aliases": {"en": [{"value": a} for a in aliases]},
        "claims": claims or {"P31": [item("Q7397")]},
        "lastrevid": 42,
    }


LABELS = {"Q1": {"yamaha", "yamahacorporation"}, "Q2": {"macos"}, "Q3": {"microsoftwindows"},
          "Q4": {"apachelicense20"}}


def check(rec: dict[str, Any], category: str, ent: dict[str, Any]) -> itemcheck.Decision:
    return itemcheck.check_record(rec, category, ent, LABELS)


def test_software_confirms_on_name_and_one_field():
    ent = entity("Vocaloid", P178=[item("Q1")], P306=[item("Q2"), item("Q3")],
                 P577=[when("+2004-00-00T00:00:00Z", 9)])
    rec = {"name": "Vocaloid", "release_date": "2004-01-01", "developers": ["Yamaha"],
           "operating_systems": ["macOS", "Microsoft Windows"]}
    got = check(rec, "software", ent)
    assert got.decision == CONFIRM
    assert set(got.agreements) == {"release_date", "developers", "operating_systems"}
    assert check({"name": "Vocaloid", "developers": ["Yamaha"]}, "software", ent).decision \
        == CONFIRM


def test_name_must_be_a_label_or_alias():
    ent = entity("Apple Home", ("HomeKit",), P178=[item("Q1")])
    rec = {"name": "Apple HomeKit", "developers": ["Yamaha"]}
    assert check(rec, "software", ent).decision == itemcheck.NAME_MISMATCH
    assert check({**rec, "name": "HomeKit"}, "software", ent).decision == CONFIRM


def test_name_alone_is_insufficient():
    assert check({"name": "Vocaloid"}, "software", entity("Vocaloid")).decision == INSUFFICIENT


def test_a_listed_name_the_item_does_not_claim_is_a_conflict():
    ent = entity("Tesseract", P275=[item("Q4")], P306=[item("Q2")])
    rec = {"name": "Tesseract", "licenses": ["GPL"], "operating_systems": ["macOS"]}
    got = check(rec, "software", ent)
    assert got.decision == CONTRADICT and got.conflicts == ("licenses",)


def test_unresolved_claims_are_no_evidence():
    ent = entity("Tesseract", P275=[item("Q999")])
    assert check({"name": "Tesseract", "licenses": ["GPL"]}, "software", ent).decision \
        == INSUFFICIENT


def test_missing_or_redirected_item():
    assert check({"name": "X"}, "software", {"id": "Q9", "missing": ""}).decision \
        == itemcheck.NOITEM
    assert check({"name": "X"}, "software", {"redirects": {"from": "Q9", "to": "Q8"},
                                              "claims": {"P31": []}}).decision == itemcheck.NOITEM


def test_website_homepage():
    ent = entity("Example", P856=[text("http://www.example.org/"),
                                   text("https://old.example.org", "deprecated")])
    assert check({"name": "Example", "homepage_url": "https://example.org"}, "website",
                 ent).decision == CONFIRM
    assert check({"name": "Example", "homepage_url": "https://old.example.org"}, "website",
                 ent).decision == CONTRADICT


def test_device_needs_two_fields():
    ent = entity("PowerBook 520c", ("Apple PowerBook 520c",), P176=[item("Q5")],
                 P577=[when("+1994-05-16T00:00:00Z", 11)])
    labels = {"Q5": {"appleinc", "apple"}}
    rec = {"name": "Apple PowerBook 520c", "brand": "apple"}
    assert itemcheck.check_record(rec, "laptop", ent, labels).decision == INSUFFICIENT
    rec["release_date"] = "1994-05-16"
    assert itemcheck.check_record(rec, "laptop", ent, labels).decision == CONFIRM
    rec["release_date"] = "1994-05-17"
    assert itemcheck.check_record(rec, "laptop", ent, labels).decision == CONTRADICT


def test_dates_compare_at_the_coarser_precision():
    year = [{"time": "+1988-00-00T00:00:00Z", "precision": 9}]
    day = [{"time": "+1988-03-09T00:00:00Z", "precision": 11}]
    assert itemcheck.cmp_date("1988-01-01", day) == AGREE  # a year written out
    assert itemcheck.cmp_date("1988-03-01", day) == AGREE  # a month written out
    assert itemcheck.cmp_date("1988-04-01", day) == CONFLICT
    assert itemcheck.cmp_date("1988-03-10", day) == CONFLICT
    assert itemcheck.cmp_date("1988-03-10", year) == AGREE
    assert itemcheck.cmp_date(1989, year) == CONFLICT
    assert itemcheck.cmp_date("1988", [{"time": "+1900-00-00T00:00:00Z", "precision": 7}]) == NA
    assert itemcheck.cmp_date(None, year) == NA


def test_url_key():
    assert itemcheck.url_key("HTTPS://WWW.Example.org/a/") == "example.org/a"
    assert itemcheck.url_key("http://example.org/?q=1") == "example.org?q=1"
    assert itemcheck.url_key("not a url") == ""


def test_store_batches_resolves_once_and_retries():
    calls: list[tuple[int, str]] = []
    fail = {"next": True}

    def fetch(ids: list[str], props: str) -> dict[str, Any] | None:
        calls.append((len(ids), props))
        if fail["next"]:
            fail["next"] = False
            return None
        return {q: entity(q) for q in ids}

    store = itemcheck.ItemStore(fetch, sleep=lambda _s: None)
    got = store.entities([f"Q{i}" for i in range(1, 61)])
    assert len(got) == 60 and [n for n, _p in calls] == [50, 50, 10]
    store.resolve(["Q1", "Q2"])
    store.resolve(["Q1", "Q2", "Q3"])
    assert [n for n, p in calls if p == "labels|aliases"] == [2, 1]
    assert store.labels["Q3"] == {"q3"}


def test_cited_items():
    rec = {"source_urls": ["https://www.wikidata.org/wiki/Q5", "https://example.org",
                           "https://www.wikidata.org/wiki/Q5"]}
    assert itemcheck.cited_items(rec) == ["Q5"]
    assert itemcheck.target_ids(entity("x", P178=[item("Q1")], P31=[item("Q7")]),
                                "software") == {"Q1"}


def test_promotion_follows_the_item_check():
    def decide(item_: str | None, row: str | None = None):
        return promote.decide(band="yellow", source_urls=[], url_cache={},
                              crossref_decision=None, rowcheck_decision=row,
                              itemcheck_decision=item_)

    assert decide("confirm") == promote.PromotionDecision(True, "item-confirm")
    assert decide("contradict", row="confirm").promote is False
    assert decide("confirm", row="confirm").reason == "row-confirm"
    assert decide("insufficient").promote is False
