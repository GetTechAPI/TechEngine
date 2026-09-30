"""Scoped validation: FK targets stay whole, duplicates are still caught."""

import json

from app import validate

BRAND = {"slug": "acme", "name": "Acme", "country": "US",
         "categories": ["smartphone-oem"], "source_urls": ["https://example.com"]}


def _put(root, rel, rec):
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rec), encoding="utf-8")


def _site(tmp_path, monkeypatch):
    monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
    _put(tmp_path, "brand/us/acme.json", BRAND)
    _put(tmp_path, "website/a/one.json", {"slug": "one"})
    _put(tmp_path, "website/a/two.json", {"slug": "two"})


def test_scoped_only_checks_changed_files(tmp_path, monkeypatch):
    _site(tmp_path, monkeypatch)
    full = validate.validate()
    scoped = validate.validate({"website/a/one.json"})
    # full run flags both incomplete websites, scoped run only the changed one
    assert any("two.json" in e for e in full)
    assert not any("two.json" in e for e in scoped)
    assert any("one.json" in e for e in scoped)


def test_scoped_finds_duplicate_slug_in_unchanged_file(tmp_path, monkeypatch):
    _site(tmp_path, monkeypatch)
    _put(tmp_path, "website/b/one.json", {"slug": "one"})
    errs = validate.validate({"website/b/one.json"})
    assert any("duplicate website slug 'one'" in e for e in errs)


def test_scoped_empty_change_set_passes_fk_categories_only(tmp_path, monkeypatch):
    _site(tmp_path, monkeypatch)
    assert not any("website" in e for e in validate.validate(set()))
