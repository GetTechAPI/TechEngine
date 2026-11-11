import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.categories import CATEGORIES
from app.verify import cli, offline
from app.verify.common import CATEGORIES as VERIFY_CATEGORIES
from app.verify.common import Record


def test_all_categories_share_registry():
    assert VERIFY_CATEGORIES is CATEGORIES
    assert len(CATEGORIES) == len(set(CATEGORIES)) == 13
    assert set(CATEGORIES) == {
        "smartphone", "tablet", "watch", "pda", "cpu", "gpu", "soc", "laptop",
        "monitor", "software", "website", "brand", "device_catalog",
    }


@pytest.mark.parametrize("category", ["future"])
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
    assert "13/13 categories" in result.stdout
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
