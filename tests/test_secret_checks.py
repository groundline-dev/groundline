"""Exercise Git filename handling and tracked snapshots without installing Go."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.secrets_smoke import CHECK, ROOT, git, init_repo

BLOCKED_NAMES = [
    ".env",
    ".env-old",
    "prod.env",
    "a/b/.env.local",
    ".env.example.backup",
]
ALLOWED_NAMES = [".env.example", "nested/.env.example", "environment.py", "env.txt"]


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    init_repo(repository, ".betterleaks.toml", ".gitignore")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    fake = binaries / "betterleaks"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "record = {'args': sys.argv[1:]}\n"
        "if sys.argv[1] == 'dir':\n"
        "    root = pathlib.Path(sys.argv[-1])\n"
        "    record['files'] = {str(p.relative_to(root)): p.read_text() "
        "for p in root.rglob('*') if p.is_file()}\n"
        "pathlib.Path(os.environ['SCAN_RECORD']).write_text(json.dumps(record))\n"
        "sys.exit(int(os.environ.get('SCAN_EXIT', '0')))\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binaries}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SCAN_RECORD", str(tmp_path / "scan.json"))
    return repository


def scan(
    repo: Path, mode: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), mode],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def scan_record(repo: Path) -> dict[str, Any]:
    return json.loads((repo.parent / "scan.json").read_text())


@pytest.mark.parametrize("name", BLOCKED_NAMES)
def test_empty_environment_files_are_ignored_and_blocked(repo: Path, name: str):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    assert git(repo, "check-ignore", "--", name) == name
    git(repo, "add", "-f", "--", name)
    result = scan(repo, "staged")
    assert result.returncode == 1
    assert f"{name}:env-file:0" in result.stderr
    assert scan(repo, "tracked").returncode == 1
    git(repo, "commit", "-qm", "test: force-add environment file")
    git(repo, "rm", "--", name)
    assert scan(repo, "staged").returncode == 0  # Cleanup is allowed.
    git(repo, "commit", "-qm", "test: remove environment file")
    assert scan(repo, "history").returncode == 1  # Deleting doesn't erase exposure.


def test_rename_without_content_changes_is_blocked(repo: Path):
    (repo / "sample.txt").write_text("ordinary placeholder\n")
    git(repo, "add", "sample.txt")
    git(repo, "commit", "-qm", "test: add sample")
    git(repo, "mv", "sample.txt", ".env-renamed")
    assert scan(repo, "staged").returncode == 1
    git(repo, "commit", "-qm", "test: rename sample")
    assert scan(repo, "history").returncode == 1


def test_history_includes_other_refs_and_root_commits(repo: Path):
    original = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "--orphan", "another-branch")
    (repo / "archived.env").touch()
    git(repo, "add", "-f", "archived.env")
    git(repo, "commit", "-qm", "test: root with environment file")
    git(repo, "checkout", "-q", "--detach", original)
    assert scan(repo, "tracked").returncode == 0
    assert scan(repo, "history").returncode == 1


def test_examples_are_allowed_at_all_depths(repo: Path):
    for name in ALLOWED_NAMES:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PLACEHOLDER=\n")
        git(repo, "add", "--", name)
    assert scan(repo, "staged").returncode == 0
    assert scan(repo, "tracked").returncode == 0


def test_every_exception_names_one_commit():
    # Betterleaks also accepts PATH:RULE:LINE without a commit, which exempts that
    # line in every commit, including future ones. Allow only COMMIT:PATH:RULE:LINE.
    text = (ROOT / ".betterleaksignore").read_text()
    entries = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert entries
    for entry in entries:
        assert re.fullmatch(r"[0-9a-f]{40}:[^:]+:[a-z0-9-]+:\d+", entry), entry


def test_staged_check_uses_index_names(repo: Path):
    (repo / "example.txt").write_text("placeholder\n")
    git(repo, "add", "example.txt")
    (repo / "example.txt").rename(repo / ".env-unstaged")
    assert scan(repo, "staged").returncode == 0


def test_tracked_scan_uses_working_contents_and_excludes_untracked_files(repo: Path):
    (repo / "tracked.txt").write_text("staged version\n")
    git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("working version\n")
    (repo / ".env").write_text("local only\n")
    (repo / "untracked.txt").write_text("not tracked\n")
    assert scan(repo, "tracked").returncode == 0
    record = scan_record(repo)
    assert record["files"]["tracked.txt"] == "working version\n"
    assert ".env" not in record["files"]
    assert "untracked.txt" not in record["files"]
    assert "--redact=100" in record["args"]
    assert str(repo / ".betterleaks.toml") in record["args"]


def test_symlinks_scan_link_text_without_reading_external_targets(repo: Path):
    external = repo.parent / "outside.txt"
    external.write_text("outside the repository\n")
    (repo / "link").symlink_to(external)
    git(repo, "add", "link")
    assert scan(repo, "tracked").returncode == 0
    record = scan_record(repo)
    assert record["files"]["link"] == str(external)


def test_historical_exceptions_are_specific_to_one_commit_and_path(repo: Path):
    (repo / "sample.env").touch()
    git(repo, "add", "-f", "sample.env")
    git(repo, "commit", "-qm", "test: harmless historical file")
    commit = git(repo, "rev-parse", "HEAD")
    (repo / ".betterleaksignore").write_text(
        f"# Reviewed empty historical fixture\n{commit}:sample.env:env-file:0\n"
    )
    assert scan(repo, "history").returncode == 0
    assert scan(repo, "tracked").returncode == 1
    (repo / "sample.env").write_text("new contents\n")
    git(repo, "add", "sample.env")
    git(repo, "commit", "-qm", "test: later change is not exempt")
    assert scan(repo, "history").returncode == 1


@pytest.mark.parametrize("code", [1, 2])
def test_scanner_failures_are_not_reported_as_success(
    repo: Path, monkeypatch: pytest.MonkeyPatch, code: int
):
    monkeypatch.setenv("SCAN_EXIT", str(code))
    assert scan(repo, "tracked").returncode == code


def test_missing_scanner_fails_closed(repo: Path):
    (repo.parent / "bin" / "betterleaks").unlink()
    # Keep Git available without accidentally finding an installed scanner.
    git_binary = shutil.which("git")
    assert git_binary
    (repo.parent / "bin" / "git").symlink_to(git_binary)
    result = scan(repo, "tracked", {**os.environ, "PATH": str(repo.parent / "bin")})
    assert result.returncode == 2
    assert "could not finish" in result.stderr
