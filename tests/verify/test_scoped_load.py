"""Scoped loading reads only the requested files (plus whole FK categories)."""

import json

from app import validate
from app.verify import common


def _write(root, rel, slug):
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"slug": slug}), encoding="utf-8")


def test_only_restricts_non_fk_categories(tmp_path, monkeypatch):
    monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
    monkeypatch.setattr(common, "_load", validate._load)
    _write(tmp_path, "cpu/a/1.json", "a1")
    _write(tmp_path, "cpu/a/2.json", "a2")
    _write(tmp_path, "soc/x/1.json", "s1")
    _write(tmp_path, "soc/x/2.json", "s2")

    full = common.load_all(("cpu", "soc"))
    assert [len(full[c]) for c in ("cpu", "soc")] == [2, 2]

    scoped = common.load_all(("cpu", "soc"), only={"cpu/a/2.json", "cpu/missing.json"})
    assert [r.slug for r in scoped["cpu"]] == ["a2"]
    assert len(scoped["soc"]) == 2  # FK category stays whole

    assert common.load_all(("cpu",), only=set())["cpu"] == []
