"""Offline tests for the GSMArena URL backfill gate.

No network. Heading checks go through crossref._heading_matches, and liveness
goes through http_check.classify.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.verify import ledger
from app.verify.crossref import Candidate
from app.verify.gsmarena_backfill import (
    CONFIRM,
    CONTRADICT,
    NOTFOUND,
    GsmarenaFetcher,
    PhoneIndex,
    PoliteClient,
    add_source_url_text,
    backfill,
    compare_specs,
    content_hash,
    gate_page,
    parse_cat_file_batch,
    parse_page,
    parse_phone_url,
    parse_retry_after,
    parse_search_results,
    parse_sitemap_xml,
    sample_diverse,
)

DX650_HTML = """
<html><head><title>Acer DX650 - Full phone specifications</title></head><body>
<td data-spec="modelname">Acer DX650</td>
<td data-spec="batdescription1">Removable Li-Ion 1260 mAh battery</td>
<span data-spec="batsize-hl">1260</span>
<td data-spec="displaysize">2.8 inches, 24.3 cm<sup>2</sup> (~37.5% screen-to-body ratio)</td>
<td data-spec="displayresolution">240 x 320 pixels, 4:3 ratio (~143 ppi density)</td>
<td data-spec="weight">133 g (4.69 oz)</td>
<td data-spec="internalmemory">256MB RAM, 512MB ROM</td>
</body></html>
"""

DX650_RECORD = {
    "slug": "acer-dx650",
    "name": "Acer DX650",
    "battery_mah": 1260,
    "ram_gb": 1,
    "weight_g": 133.0,
    "display": {"size_inch": 2.8, "resolution": "240x320"},
    "variant": {"source_category": "gsmarena-kaggle"},
    "source_urls": ["https://www.kaggle.com/datasets/arwinneil/gsmarena-phone-dataset"],
}

SITEMAP = """<?xml version="1.0"?>
<urlset>
<url><loc>https://www.gsmarena.com/acer_dx650-2888.php</loc></url>
<url><loc>https://www.gsmarena.com/acer_dx650-pictures-2888.php</loc></url>
<url><loc>https://www.gsmarena.com/related.php3?idPhone=2888</loc></url>
<url><loc>https://www.gsmarena.com/nokia_3210_(1999)-6.php</loc></url>
<url><loc>https://www.gsmarena.com/acer_liquid-2963.php</loc></url>
<url><loc>https://www.gsmarena.com/acer_liquid_e-3514.php</loc></url>
</urlset>
"""


def test_parse_phone_url_rejects_non_spec_pages():
    assert parse_phone_url("https://www.gsmarena.com/acer_dx650-2888.php") == ("acer_dx650", "2888")
    assert parse_phone_url("https://www.gsmarena.com/nokia_3210_(1999)-6.php") == (
        "nokia_3210_(1999)",
        "6",
    )
    assert parse_phone_url("https://www.gsmarena.com/acer_dx650-pictures-2888.php") is None
    assert parse_phone_url("https://www.gsmarena.com/related.php3?idPhone=2888") is None


def test_sitemap_index_keeps_spec_pages_only():
    index, children = parse_sitemap_xml(SITEMAP)
    assert children == []
    assert index.phones["acer_dx650"] == (
        "2888",
        "https://www.gsmarena.com/acer_dx650-2888.php",
    )
    assert "nokia_3210_(1999)" in index.phones
    assert all("pictures" not in url for _slug, (_id, url) in index.phones.items())


def test_exact_slug_beats_suffix_siblings_and_does_not_search():
    index, _children = parse_sitemap_xml(SITEMAP)
    calls: list[str] = []

    def search(name: str) -> list[Candidate]:
        calls.append(name)
        return []

    fetcher = GsmarenaFetcher(index, search_fn=search)
    found = fetcher.search("Acer Liquid")
    assert calls == []
    assert len(found) == 1
    assert found[0].url.endswith("/acer_liquid-2963.php")


def test_ambiguous_index_uses_site_search_and_does_not_guess():
    # Both slugs end with the record name, and neither is an exact heading.
    index = PhoneIndex()
    index.phones["nokia_lumia"] = ("1", "https://www.gsmarena.com/nokia_lumia-1.php")
    index.phones["microsoft_lumia"] = ("2", "https://www.gsmarena.com/microsoft_lumia-2.php")
    calls: list[str] = []

    def search(name: str) -> list[Candidate]:
        calls.append(name)
        return [
            Candidate("Nokia Lumia", "https://www.gsmarena.com/nokia_lumia-1.php"),
            Candidate("Lumia", "https://www.gsmarena.com/lumia-9.php"),
        ]

    found = GsmarenaFetcher(index, search_fn=search).search("Lumia")
    assert calls == ["Lumia"]
    assert len(found) == 1
    assert found[0].url.endswith("/lumia-9.php")


def test_no_index_hit_does_not_call_search():
    index, _children = parse_sitemap_xml(SITEMAP)
    calls: list[str] = []
    fetcher = GsmarenaFetcher(index, search_fn=lambda name: calls.append(name) or [])
    assert fetcher.search("Completely Unknown Handset") == []
    assert calls == []


def test_unresolved_search_stays_ambiguous():
    index = PhoneIndex()
    index.phones["nokia_lumia"] = ("1", "https://www.gsmarena.com/nokia_lumia-1.php")
    index.phones["microsoft_lumia"] = ("2", "https://www.gsmarena.com/microsoft_lumia-2.php")
    fetcher = GsmarenaFetcher(
        index,
        search_fn=lambda _name: [
            Candidate("Nokia Lumia", "https://www.gsmarena.com/nokia_lumia-1.php"),
            Candidate("Microsoft Lumia", "https://www.gsmarena.com/microsoft_lumia-2.php"),
        ],
    )
    found = fetcher.search("Lumia")
    assert len(found) == 2


def test_parse_page_and_confirm_gate():
    page = parse_page(DX650_HTML)
    assert page.title == "Acer DX650"
    assert page.battery_mah == 1260
    assert page.size_inch == 2.8
    assert page.resolution == (240, 320)
    assert page.weight_g == 133
    assert page.ram_gb and abs(page.ram_gb[0] - 0.25) < 0.01
    agreements, conflicts = compare_specs(DX650_RECORD, page)
    assert "battery_mah" in agreements
    assert "display_size_inch" in agreements
    assert "ram_gb" in conflicts  # record says 1 GB, page says 256 MB
    result = gate_page(
        DX650_RECORD,
        Candidate("acer dx650", "https://www.gsmarena.com/acer_dx650-2888.php"),
        200,
        "https://www.gsmarena.com/acer_dx650-2888.php",
        DX650_HTML,
    )
    assert result.decision == CONFIRM
    assert result.proposed_url == "https://www.gsmarena.com/acer_dx650-2888.php"
    assert result.suffix_only is False


def test_spec_conflict_is_contradict_and_proposes_nothing():
    record = {
        "name": "Acer DX650",
        "battery_mah": 4000,
        "weight_g": 200,
        "display": {"size_inch": 6.5, "resolution": "1080x2400"},
    }
    result = gate_page(
        record,
        Candidate("Acer DX650", "https://www.gsmarena.com/acer_dx650-2888.php"),
        200,
        "https://www.gsmarena.com/acer_dx650-2888.php",
        DX650_HTML,
    )
    assert result.decision == CONTRADICT
    assert result.proposed_url is None


def test_title_mismatch_and_dead_url_propose_nothing():
    dead = gate_page(
        DX650_RECORD,
        Candidate("Acer DX650", "https://www.gsmarena.com/acer_dx650-2888.php"),
        404,
        "https://www.gsmarena.com/acer_dx650-2888.php",
        DX650_HTML,
    )
    assert dead.decision == NOTFOUND and dead.proposed_url is None
    mismatched = gate_page(
        {"name": "Acer Liquid", "battery_mah": 1350},
        Candidate("acer liquid", "https://www.gsmarena.com/acer_liquid-2963.php"),
        200,
        "https://www.gsmarena.com/acer_liquid-2963.php",
        DX650_HTML,
    )
    assert mismatched.decision != CONFIRM
    assert mismatched.proposed_url is None


def test_title_match_without_specs_is_ambiguous():
    html = "<html><title>Acer DX650 - Full phone specifications</title></html>"
    result = gate_page(
        {"name": "Acer DX650"},
        Candidate("Acer DX650", "https://www.gsmarena.com/acer_dx650-2888.php"),
        200,
        "https://www.gsmarena.com/acer_dx650-2888.php",
        html,
    )
    assert result.decision != CONFIRM
    assert result.proposed_url is None


def test_maker_prefix_suffix_still_matches():
    html = DX650_HTML.replace("Acer DX650", "DX650")
    result = gate_page(
        {**DX650_RECORD, "name": "Acer DX650"},
        Candidate("dx650", "https://www.gsmarena.com/acer_dx650-2888.php"),
        200,
        "https://www.gsmarena.com/acer_dx650-2888.php",
        html,
    )
    assert result.decision == CONFIRM
    assert result.suffix_only is True


def test_search_result_parser_reads_redirect_targets():
    html = """
    <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.gsmarena.com%2Facer_dx650-2888.php&amp;rut=1">
    Acer DX650 - Full phone specifications</a>
    <a href="https://www.gsmarena.com/acer_dx650-pictures-2888.php">pictures</a>
    """
    found = parse_search_results(html)
    assert len(found) == 1
    assert found[0].url == "https://www.gsmarena.com/acer_dx650-2888.php"
    assert found[0].title == "Acer DX650"


def test_sample_diverse_round_robins_brands():
    paths = [
        "data/smartphone/samsung/2017/a/samsung-a.json",
        "data/smartphone/samsung/2017/b/samsung-b.json",
        "data/smartphone/htc/2011/a/htc-a.json",
        "data/smartphone/nokia/2009/a/nokia-a.json",
    ]
    picked = sample_diverse(paths, 3)
    brands = [path.split("/")[2] for path in picked]
    assert brands == ["htc", "nokia", "samsung"]


def test_source_url_edit_is_surgical_and_idempotent():
    raw = (
        '{\n'
        '  "name": "Acer Liquid",\n'
        '  "source_urls": [\n'
        '    "https://www.kaggle.com/datasets/arwinneil/gsmarena-phone-dataset"\n'
        "  ]\n"
        "}\n"
    )
    url = "https://www.gsmarena.com/acer_liquid-2963.php"
    updated = add_source_url_text(raw, url)
    assert updated is not None
    assert '"name": "Acer Liquid"' in updated
    assert updated.count("\n") == raw.count("\n") + 1
    assert "kaggle.com" in updated and url in updated
    assert add_source_url_text(updated, url) is None


def test_cat_file_batch_parser():
    body = b'{"slug": "acer-liquid"}\n'
    blob = f"abc blob {len(body) - 1}\n".encode() + body
    missing = b"HEAD:data/smartphone/missing.json missing\n"
    parsed = parse_cat_file_batch(
        blob + missing,
        [
            "data/smartphone/acer/2009/liquid/acer-liquid.json",
            "data/smartphone/missing.json",
        ],
    )
    assert list(parsed) == ["data/smartphone/acer/2009/liquid/acer-liquid.json"]
    assert parsed["data/smartphone/acer/2009/liquid/acer-liquid.json"]["slug"] == "acer-liquid"


class _FakeClient:
    def __init__(self, pages: dict[str, tuple[int, str]]):
        self.pages = pages
        self.requests = 0
        self.urls: list[str] = []

    def fetch(self, url: str) -> tuple[int | None, str, str]:
        self.requests += 1
        self.urls.append(url)
        status, body = self.pages.get(url, (404, ""))
        return status, url, body


class _RetryAfterClient(_FakeClient):
    def __init__(self, pages: list[tuple[int, str, float | None]]):
        super().__init__({})
        self.pages = pages
        self.retry_after_s: float | None = None

    def fetch(self, url: str) -> tuple[int | None, str, str]:
        self.requests += 1
        self.urls.append(url)
        status, body, self.retry_after_s = self.pages.pop(0)
        return status, url, body


def _index_file(path: Path) -> None:
    index, _children = parse_sitemap_xml(SITEMAP)
    path.write_text(json.dumps(index.to_json()), encoding="utf-8")


def test_dry_run_does_not_write_the_record_and_caches(tmp_path: Path):
    repo = tmp_path / "techapi"
    rel = "data/smartphone/acer/2009/dx650/acer-dx650.json"
    target = repo / rel
    target.parent.mkdir(parents=True)
    original = json.dumps(DX650_RECORD, indent=2) + "\n"
    target.write_text(original, encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    _index_file(state / "phone_index.json")
    client = _FakeClient(
        {"https://www.gsmarena.com/acer_dx650-2888.php": (200, DX650_HTML)}
    )
    result = backfill(
        repo=repo,
        cache_path=state / "gsmarena_backfill_cache.jsonl",
        index_path=state / "phone_index.json",
        summary_path=state / "summary.md",
        limit=5,
        sleep_s=0,
        dry_run=True,
        refresh_index=False,
        client=client,
        paths=[rel],
        records={rel: DX650_RECORD},
    )
    assert target.read_text(encoding="utf-8") == original
    assert result.counts()[CONFIRM] == 1
    assert result.rows[0]["proposed_url"].endswith("/acer_dx650-2888.php")
    cached = ledger.iter_entries(state / "gsmarena_backfill_cache.jsonl")
    assert next(cached)["decision"] == CONFIRM
    # Resume must not hit the network again.
    client.requests = 0
    again = backfill(
        repo=repo,
        cache_path=state / "gsmarena_backfill_cache.jsonl",
        index_path=state / "phone_index.json",
        summary_path=state / "summary.md",
        limit=5,
        sleep_s=0,
        dry_run=True,
        refresh_index=False,
        client=client,
        paths=[rel],
        records={rel: DX650_RECORD},
    )
    assert client.requests == 0
    assert again.cached == 1
    assert content_hash(DX650_RECORD) == again.rows[0]["decision"] or again.cached == 1


def test_retry_after_is_parsed_and_honored_without_a_fixed_delay(tmp_path: Path, monkeypatch):
    assert parse_retry_after("12") == 12
    polite = PoliteClient(sleep_s=0, get=lambda _url: (429, _url, "", "7"))
    polite.fetch("https://example.invalid")
    assert polite.retry_after_s == 7

    repo = tmp_path / "techapi"
    rel = "data/smartphone/acer/2009/dx650/acer-dx650.json"
    target = repo / rel
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(DX650_RECORD), encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    _index_file(state / "phone_index.json")
    client = _RetryAfterClient(
        [(429, "", 2), (200, DX650_HTML, None)]
    )
    waits: list[float] = []
    monkeypatch.setattr("app.verify.gsmarena_backfill.time.sleep", waits.append)
    result = backfill(
        repo=repo, cache_path=state / "cache.jsonl", index_path=state / "phone_index.json",
        summary_path=state / "summary.md", limit=1, sleep_s=0, dry_run=True,
        refresh_index=False, client=client, paths=[rel], records={rel: DX650_RECORD},
    )
    assert waits == [2]
    assert client.requests == 2
    assert result.counts()[CONFIRM] == 1


def test_long_retry_after_stops_without_retrying_or_waiting(tmp_path: Path, monkeypatch):
    repo = tmp_path / "techapi"
    rel = "data/smartphone/acer/2009/dx650/acer-dx650.json"
    target = repo / rel
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(DX650_RECORD), encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    _index_file(state / "phone_index.json")
    client = _RetryAfterClient([(429, "", 601)])
    def must_not_wait(_seconds: float) -> None:
        raise AssertionError("must not wait")

    monkeypatch.setattr("app.verify.gsmarena_backfill.time.sleep", must_not_wait)
    result = backfill(
        repo=repo, cache_path=state / "cache.jsonl", index_path=state / "phone_index.json",
        summary_path=state / "summary.md", limit=1, sleep_s=0, dry_run=True,
        refresh_index=False, client=client, paths=[rel], records={rel: DX650_RECORD},
    )
    assert client.requests == 1
    assert result.stopped and "Retry-After=601s" in result.stopped


def test_write_mode_does_not_cache_confirm_when_sparse_file_is_missing(tmp_path: Path):
    repo = tmp_path / "techapi"
    rel = "data/smartphone/acer/2009/dx650/acer-dx650.json"
    state = tmp_path / "state"
    state.mkdir()
    _index_file(state / "phone_index.json")
    client = _FakeClient({"https://www.gsmarena.com/acer_dx650-2888.php": (200, DX650_HTML)})
    result = backfill(
        repo=repo, cache_path=state / "cache.jsonl", index_path=state / "phone_index.json",
        summary_path=state / "summary.md", limit=1, sleep_s=0, dry_run=False,
        refresh_index=False, client=client, paths=[rel], records={rel: DX650_RECORD},
    )
    cached = next(ledger.iter_entries(state / "cache.jsonl"))
    assert result.rows[0]["decision"] == "write-failed"
    assert cached["decision"] == "write-failed"


def test_write_mode_skips_a_worktree_file_changed_since_head_read(tmp_path: Path):
    repo = tmp_path / "techapi"
    rel = "data/smartphone/acer/2009/dx650/acer-dx650.json"
    target = repo / rel
    target.parent.mkdir(parents=True)
    changed = {**DX650_RECORD, "battery_mah": 9999}
    target.write_text(json.dumps(changed, indent=2), encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    _index_file(state / "phone_index.json")
    client = _FakeClient({"https://www.gsmarena.com/acer_dx650-2888.php": (200, DX650_HTML)})
    result = backfill(
        repo=repo, cache_path=state / "cache.jsonl", index_path=state / "phone_index.json",
        summary_path=state / "summary.md", limit=1, sleep_s=0, dry_run=False,
        refresh_index=False, client=client, paths=[rel], records={rel: DX650_RECORD},
    )
    assert result.rows[0]["decision"] == "write-failed"
    assert "gsmarena.com/acer_dx650-2888.php" not in target.read_text(encoding="utf-8")


def test_write_mode_appends_only_a_confirm(tmp_path: Path):
    repo = tmp_path / "techapi"
    rel = "data/smartphone/acer/2009/dx650/acer-dx650.json"
    target = repo / rel
    target.parent.mkdir(parents=True)
    raw = json.dumps(DX650_RECORD, indent=2) + "\n"
    target.write_text(raw, encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    _index_file(state / "phone_index.json")
    client = _FakeClient(
        {"https://www.gsmarena.com/acer_dx650-2888.php": (200, DX650_HTML)}
    )
    backfill(
        repo=repo,
        cache_path=state / "cache.jsonl",
        index_path=state / "phone_index.json",
        summary_path=state / "summary.md",
        limit=1,
        sleep_s=0,
        dry_run=False,
        refresh_index=False,
        client=client,
        paths=[rel],
        records={rel: DX650_RECORD},
    )
    written = target.read_text(encoding="utf-8")
    assert "https://www.gsmarena.com/acer_dx650-2888.php" in written
    assert "kaggle.com" in written
    assert '"name": "Acer DX650"' in written
    cached = next(ledger.iter_entries(state / "cache.jsonl"))
    assert cached["decision"] == CONFIRM
