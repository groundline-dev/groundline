"""Scan staged changes, tracked working files, or all fetched Git history.

The pinned pre-commit environment supplies Gitleaks locally; CI installs the same
release. Git filenames are checked separately because patch scans omit empty
files and content-preserving renames. No application dependencies are needed.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

# Names of added or changed files, so staged and history modes agree.
CHANGED_NAMES = ("--name-only", "-z", "--no-renames", "--diff-filter=ACMT")


def git(root: Path, *args: str, stdin: bytes = b"") -> bytes:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, input=stdin, stdout=subprocess.PIPE
    ).stdout


def paths(output: bytes) -> list[str]:
    return [os.fsdecode(name) for name in output.split(b"\0") if name]


def forbidden(path: str) -> bool:
    name = PurePosixPath(path).name
    return name != ".env.example" and (name.startswith(".env") or name.endswith(".env"))


def ignored_fingerprints(root: Path) -> set[str]:
    path = root / ".gitleaksignore"
    if not path.exists():
        return set()
    lines = map(str.strip, path.read_text().splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def history_findings(root: Path) -> set[str]:
    commits = git(root, "rev-list", "--all")
    # One diff-tree for all commits. With -z, each commit's SHA precedes its
    # paths; -m repeats a merge's SHA once per parent.
    output = git(
        root,
        "diff-tree",
        "--stdin",
        "--root",
        "-m",
        "-r",
        *CHANGED_NAMES,
        stdin=commits,
    )
    shas = set(commits.decode().split())
    findings: set[str] = set()
    commit = ""
    for token in paths(output):
        if token in shas:
            commit = token
        elif forbidden(token):
            findings.add(f"{commit}:{token}:env-file:0")
    return findings


def check_filenames(root: Path, mode: str, tracked: list[str]) -> bool:
    if mode == "history":
        findings = history_findings(root)
    else:
        if mode == "staged":
            tracked = paths(git(root, "diff", "--cached", *CHANGED_NAMES))
        findings = {f"{path}:env-file:0" for path in tracked if forbidden(path)}
    blocked = sorted(findings - ignored_fingerprints(root))
    for finding in blocked:
        # repr keeps unusual filenames from injecting lines into CI output.
        print(f"Blocked environment filename: {finding!r}", file=sys.stderr)
    return not blocked


def snapshot(root: Path, tracked: list[str], destination_root: Path) -> None:
    """Copy tracked working files, excluding ignored .env files and caches."""
    for name in tracked:
        source = root / name
        if not os.path.lexists(source):
            continue  # An unstaged deletion has no current contents.
        destination = destination_root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            # Git stores the link text, not the contents of its target.
            destination.write_bytes(os.fsencode(os.readlink(source)))
        elif source.is_file():
            shutil.copyfile(source, destination)
        else:
            raise ValueError(f"Cannot scan tracked path: {name!r}")


def scan(root: Path, mode: str) -> int:
    # ls-files repeats conflicted paths once per stage.
    tracked = (
        sorted(set(paths(git(root, "ls-files", "--cached", "-z"))))
        if mode == "tracked"
        else []
    )
    filenames_ok = check_filenames(root, mode, tracked)
    common = [
        "--config",
        str(root / ".gitleaks.toml"),
        "--gitleaks-ignore-path",
        str(root / ".gitleaksignore"),
        "--redact=100",
        "--no-banner",
        "--verbose",
    ]
    if mode == "tracked":
        # Scanning the checkout directly would include developers' ignored .env
        # files, caches and virtual environments.
        with tempfile.TemporaryDirectory(prefix="groundline-secrets-") as temporary:
            snapshot(root, tracked, Path(temporary))
            result = subprocess.run(["gitleaks", "dir", *common, temporary], cwd=root)
    else:
        # -m includes changes introduced only while resolving a merge.
        selection = (
            ["--staged"] if mode == "staged" else ["--log-opts=--all --full-history -m"]
        )
        result = subprocess.run(
            ["gitleaks", "git", *common, *selection, str(root)], cwd=root
        )
    return result.returncode or int(not filenames_ok)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("staged", "tracked", "history"))
    args = parser.parse_args()
    try:
        root = Path(
            os.fsdecode(git(Path.cwd(), "rev-parse", "--show-toplevel")).strip()
        )
        return scan(root, args.mode)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"Secret check could not finish: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
