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


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, stdout=subprocess.PIPE
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
    return {
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def check_filenames(root: Path, mode: str, tracked: list[str]) -> bool:
    ignored = ignored_fingerprints(root)
    findings: set[str] = set()
    if mode == "history":
        for commit in git(root, "rev-list", "--all").decode().splitlines():
            changed = paths(
                git(
                    root,
                    "diff-tree",
                    "--root",
                    "-m",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "-z",
                    "--no-renames",
                    "--diff-filter=ACMT",
                    commit,
                )
            )
            findings.update(
                f"{commit}:{path}:env-file:0" for path in changed if forbidden(path)
            )
    else:
        changed = tracked
        if mode == "staged":
            changed = paths(
                git(
                    root,
                    "diff",
                    "--cached",
                    "--name-only",
                    "-z",
                    "--no-renames",
                    "--diff-filter=ACMT",
                )
            )
        findings.update(f"{path}:env-file:0" for path in changed if forbidden(path))
    for finding in sorted(findings - ignored):
        # repr keeps unusual filenames from injecting lines into CI output.
        print(f"Blocked environment filename: {finding!r}", file=sys.stderr)
    return not (findings - ignored)


def scan(root: Path, mode: str) -> int:
    tracked = sorted(set(paths(git(root, "ls-files", "--cached", "-z"))))
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
        # Copy only tracked working files. Scanning the checkout directly would
        # include developers' ignored .env files, caches and virtual environments.
        with tempfile.TemporaryDirectory(prefix="groundline-secrets-") as temporary:
            snapshot = Path(temporary)
            for name in tracked:
                source = root / name
                if not source.exists() and not source.is_symlink():
                    continue  # An unstaged deletion has no current contents.
                destination = snapshot / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.is_symlink():
                    # Git stores the link text, not the contents of its target.
                    destination.write_bytes(os.fsencode(os.readlink(source)))
                elif source.is_file():
                    shutil.copyfile(source, destination)
                else:
                    raise ValueError(f"Cannot scan tracked path: {name!r}")
            result = subprocess.run(
                ["gitleaks", "dir", *common, str(snapshot)], cwd=root
            )
    else:
        # -m includes changes introduced only while resolving a merge.
        selection = (
            ["--staged"] if mode == "staged" else ["--log-opts=--all --full-history -m"]
        )
        result = subprocess.run(
            ["gitleaks", "git", *common, *selection, str(root)], cwd=root
        )
    return result.returncode or (0 if filenames_ok else 1)


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
