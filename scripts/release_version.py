"""Choose the version the release workflow publishes.

The release workflow's build job runs it and appends what it prints to
$GITHUB_OUTPUT.
"""

import os
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

PACKAGE = "groundline-core"
MAIN_REF = "refs/heads/main"
PYPI = "https://pypi.org"
TESTPYPI = "https://test.pypi.org"


class ReleaseRefused(Exception):
    """The run must stop before anything is uploaded."""


def tag_for(version: str) -> str:
    """The git tag for a release, named after the package so the SDK's own
    releases can be tagged in the same repository."""
    return f"{PACKAGE}-v{version}"


def release_version(
    *,
    version: str,
    ref: str,
    run_number: int,
    on_pypi: Callable[[str], bool],
    on_testpypi: Callable[[str], bool],
    tagged: Callable[[str], bool],
) -> str:
    """The version to build and upload for a run of the workflow from `ref`.

    From main it is `version`, which must not be on PyPI, on TestPyPI or tagged
    already: an index never accepts the same version twice. From anywhere else
    it is a development version, unique to the run, so a trial on TestPyPI
    doesn't use up the version the real release needs there.
    """
    if ref != MAIN_REF:
        return f"{version}.dev{run_number}"
    bump = (
        "Bump the version in pyproject.toml in a pull request, then run the "
        "release again."
    )
    if on_pypi(version):
        raise ReleaseRefused(f"{PACKAGE} {version} is already on PyPI. {bump}")
    if on_testpypi(version):
        raise ReleaseRefused(
            f"{PACKAGE} {version} is already on TestPyPI from an earlier run that "
            f"stopped before PyPI, and TestPyPI won't take it again. {bump}"
        )
    if tagged(tag_for(version)):
        raise ReleaseRefused(
            f"the tag {tag_for(version)} already exists, but {version} isn't on "
            "PyPI. Check whether an earlier release stopped partway."
        )
    return version


def _on_index(index: str) -> Callable[[str], bool]:
    def has(version: str) -> bool:
        url = f"{index}/pypi/{PACKAGE}/{version}/json"
        try:
            with urllib.request.urlopen(url, timeout=30):
                return True
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return False
            raise

    return has


def _tag_exists(tag: str) -> bool:
    # The workflow checks out every tag (fetch-depth: 0).
    rev_parse = subprocess.run(
        ["git", "rev-parse", "--quiet", "--verify", f"refs/tags/{tag}"],
        capture_output=True,
        check=False,
    )
    return rev_parse.returncode == 0


def main() -> None:
    """Print `version=…` and `tag=…` for $GITHUB_OUTPUT, or stop with a reason."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    try:
        chosen = release_version(
            version=pyproject["project"]["version"],
            ref=os.environ["GITHUB_REF"],
            run_number=int(os.environ["GITHUB_RUN_NUMBER"]),
            on_pypi=_on_index(PYPI),
            on_testpypi=_on_index(TESTPYPI),
            tagged=_tag_exists,
        )
    except ReleaseRefused as refusal:
        sys.exit(f"release refused: {refusal}")
    print(f"version={chosen}")
    print(f"tag={tag_for(chosen)}")


if __name__ == "__main__":
    main()
