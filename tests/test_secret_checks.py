"""Exercise Git filename handling and tracked snapshots without installing Go."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "check_secrets.py"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=Secret check test",
            "-c",
            "user.email=test@example.invalid",
            *args,
        ],
        cwd=repo,
        text=True,
    ).strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    git(repository, "init", "-q")
    shutil.copyfile(ROOT / ".gitleaks.toml", repository / ".gitleaks.toml")
    shutil.copyfile(ROOT / ".gitignore", repository / ".gitignore")
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "chore: initialize fixture")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    fake = binaries / "gitleaks"
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


def scan(repo: Path, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), mode], cwd=repo, capture_output=True, text=True
    )


@pytest.mark.parametrize(
    "name",
    [".env", ".env-old", "prod.env", "a/b/.env.local", ".env.example.backup"],
)
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
    for name in [".env.example", "nested/.env.example", "environment.py", "env.txt"]:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PLACEHOLDER=\n")
        git(repo, "add", "--", name)
    assert scan(repo, "staged").returncode == 0
    assert scan(repo, "tracked").returncode == 0


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
    record = json.loads((repo.parent / "scan.json").read_text())
    assert record["files"]["tracked.txt"] == "working version\n"
    assert ".env" not in record["files"]
    assert "untracked.txt" not in record["files"]
    assert "--redact=100" in record["args"]
    assert str(repo / ".gitleaks.toml") in record["args"]


def test_symlinks_scan_link_text_without_reading_external_targets(repo: Path):
    external = repo.parent / "outside.txt"
    external.write_text("outside the repository\n")
    (repo / "link").symlink_to(external)
    git(repo, "add", "link")
    assert scan(repo, "tracked").returncode == 0
    record = json.loads((repo.parent / "scan.json").read_text())
    assert record["files"]["link"] == str(external)


def test_historical_exceptions_are_specific_to_one_commit_and_path(repo: Path):
    (repo / "sample.env").touch()
    git(repo, "add", "-f", "sample.env")
    git(repo, "commit", "-qm", "test: harmless historical file")
    commit = git(repo, "rev-parse", "HEAD")
    (repo / ".gitleaksignore").write_text(
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
    (repo.parent / "bin" / "gitleaks").unlink()
    # Keep Git available without accidentally finding an installed Gitleaks.
    git_binary = shutil.which("git")
    assert git_binary
    (repo.parent / "bin" / "git").symlink_to(git_binary)
    result = subprocess.run(
        [sys.executable, str(CHECK), "tracked"],
        cwd=repo,
        env={**os.environ, "PATH": str(repo.parent / "bin")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "could not finish" in result.stderr
