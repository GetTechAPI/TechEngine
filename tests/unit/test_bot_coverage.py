import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import dump_check
from app.categories import CATEGORIES, COLLECTIONS
from app.verify import cli, offline
from app.verify.common import CATEGORIES as VERIFY_CATEGORIES
from app.verify.common import Record


def test_all_categories_share_registry():
    assert VERIFY_CATEGORIES is CATEGORIES
    assert len(CATEGORIES) == len(set(CATEGORIES)) == 12
    assert set(CATEGORIES) == {
        "smartphone", "tablet", "watch", "pda", "cpu", "gpu", "soc", "laptop",
        "monitor", "software", "website", "brand",
    }


@pytest.mark.parametrize("category", ["laptop", "monitor", "software", "website", "future"])
def test_missing_domain_rules_never_earn_green(category):
    score = offline.score_record(Record(category, "example.json", {
        "slug": "example", "name": "Example", "source_urls": ["https://intel.com/example"],
    }), 2026, {})
    assert score.band != "green"
    assert score.subscores["consistency"] == 0
    assert "domain_rules_unavailable" in score.flags
    assert offline._completeness(category, {}) == 0


def test_changed_paths_use_supplied_base_and_propagate_errors(monkeypatch, tmp_path):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout="data/laptop/example.json\ndata/_verify/status.json\n")

    monkeypatch.setattr(cli, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(cli.subprocess, "run", run)
    assert "laptop/example.json" in cli._changed_data_slugs("develop-sha")
    assert "develop-sha...HEAD" in calls[0][0]
    assert calls[0][1]["cwd"] == tmp_path

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(cli.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        cli._changed_data_slugs("missing-base")


def write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def fixture_dump(tmp_path):
    collections = {}
    for category, resource in COLLECTIONS.items():
        write(tmp_path / "data" / category / "sample.json", {"slug": "sample"})
        write(tmp_path / "site/public/v1" / resource / "index.json",
              {"count": 1, "results": [{"slug": "sample"}]})
        collections[resource] = {"count": 1}
    write(tmp_path / "site/public/v1/index.json", {"collections": collections})


def test_dump_checks_parse_manifest_and_indices(monkeypatch, tmp_path):
    fixture_dump(tmp_path)
    changed = "site/public/v1/laptops/sample/index.json"
    write(tmp_path / changed, {"slug": "sample"})
    monkeypatch.setattr(dump_check.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(stdout=changed + "\n"))
    assert dump_check.check_dump(tmp_path, "base") == []
    (tmp_path / changed).write_text("{broken", encoding="utf-8")
    assert any(changed in e for e in dump_check.check_dump(tmp_path, "base"))
    write(tmp_path / changed, {})
    write(tmp_path / "site/public/v1/laptops/index.json", {"count": 2, "results": []})
    assert any("laptops: index" in e for e in dump_check.check_dump(tmp_path, "base"))
    write(tmp_path / "site/public/v1/index.json", {"collections": {}})
    assert sum("manifest count" in e for e in dump_check.check_dump(tmp_path, "base")) == 12


def test_dump_deletion_still_checks_counts(monkeypatch, tmp_path):
    fixture_dump(tmp_path)
    replies = iter(["", "site/public/v1/laptops/index.json\n"])
    monkeypatch.setattr(dump_check.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(stdout=next(replies)))
    (tmp_path / "site/public/v1/laptops/index.json").unlink()
    assert any("laptops" in e for e in dump_check.check_dump(tmp_path, "base"))


def test_dump_no_changes_skips_missing_dump(monkeypatch, tmp_path):
    monkeypatch.setattr(dump_check.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(stdout=""))
    assert dump_check.check_dump(tmp_path, "base") == []


def test_scope_includes_all_records_and_base(tmp_path):
    fixture_dump(tmp_path)
    assert dump_check.scope(tmp_path, "develop", "abcdef1234") == (
        "12/12 categories ? 12 records ? diff base develop@abcdef1"
    )


def test_pr_base_excludes_prior_develop_changes_and_release_includes_them(monkeypatch, tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    write(tmp_path / "data/gpu/old.json", {"slug": "old"})
    git("add", ".")
    git("commit", "-m", "initial")
    main = git("rev-parse", "HEAD")
    git("switch", "-c", "develop")
    write(tmp_path / "data/laptop/prior.json", {"slug": "prior"})
    git("add", ".")
    git("commit", "-m", "prior develop work")
    develop = git("rev-parse", "HEAD")
    git("switch", "-c", "feature")
    write(tmp_path / "data/website/own.json", {"slug": "own"})
    git("add", ".")
    git("commit", "-m", "own work")
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path / "data")
    assert cli._changed_data_slugs(develop) == {"website/own.json"}
    assert cli._changed_data_slugs(main) == {"laptop/prior.json", "website/own.json"}


def test_integrity_scans_non_chip_categories(tmp_path):
    for category in ("laptop", "monitor", "software", "website"):
        write(tmp_path / category / "a.json",
              {"slug": "wrong", "name": "Example", "verified": True, "source_urls": []})
        write(tmp_path / category / "b.json", {"slug": "wrong", "name": "Example"})
    report = tmp_path / "hard.json"
    result = subprocess.run(
        ["python", "integrity_check.py", str(tmp_path), "--hard-report", str(report)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    assert "12/12 categories" in result.stdout
    anomalies = json.loads(report.read_text(encoding="utf-8"))
    for category in ("laptop", "monitor", "software", "website"):
        assert any(f"[{category}] DUP slug" in item for item in anomalies)
        assert any(f"[{category}] slug!=file" in item for item in anomalies)
        assert any(f"[{category}] verified without sources" in item for item in anomalies)
    assert not any("DUP name" in item for item in anomalies)


def test_report_recomputes_without_committed_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "SCORES_PATH", tmp_path / "missing.jsonl")
    calls = []
    monkeypatch.setattr(cli, "cmd_score", lambda args: calls.append(args) or 0)
    assert cli.cmd_report(SimpleNamespace()) == 0
    assert calls[0].no_cache is True
    assert calls[0].changed is False
