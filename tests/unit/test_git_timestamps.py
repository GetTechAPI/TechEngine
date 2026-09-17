"""Timestamps come from git, so a re-dump of unchanged data is byte-identical."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from app.seed import _git_timestamps


def _git(repo: Path, *args: str, when: str | None = None) -> None:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "PATH": os.environ["PATH"],
    }
    if when:
        # _git_timestamps reads committer dates, so both must be pinned.
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _repo(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    (data / "cpu").mkdir(parents=True)
    _git(tmp_path, "init", "-q")
    return data


def _commit(repo: Path, rel: str, payload: dict, when: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    _git(repo.parent, "add", "-A")
    _git(repo.parent, "commit", "-q", "-m", rel, when=when)


def test_created_and_updated_track_first_and_last_commit(tmp_path):
    data = _repo(tmp_path)
    _commit(data, "cpu/a.json", {"slug": "a"}, "2026-01-02T03:04:05+00:00")
    _commit(data, "cpu/b.json", {"slug": "b"}, "2026-02-02T03:04:05+00:00")
    _commit(data, "cpu/a.json", {"slug": "a", "cores": 8}, "2026-03-02T03:04:05+00:00")

    stamps = _git_timestamps(data)
    created_a, updated_a = stamps["cpu/a.json"]
    created_b, updated_b = stamps["cpu/b.json"]

    assert created_a < updated_a          # edited later
    assert created_b == updated_b         # written once
    assert created_a < created_b          # a came first


def test_repeated_reads_agree(tmp_path):
    data = _repo(tmp_path)
    _commit(data, "cpu/a.json", {"slug": "a"}, "2026-01-02T03:04:05+00:00")
    assert _git_timestamps(data) == _git_timestamps(data)


def test_outside_a_git_repo_returns_nothing(tmp_path):
    plain = tmp_path / "plain"
    (plain / "cpu").mkdir(parents=True)
    assert _git_timestamps(plain) == {}
