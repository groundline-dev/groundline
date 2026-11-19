"""Choose the version the release workflow publishes.

The release workflow's build job runs it and appends what it prints to
$GITHUB_OUTPUT.
"""

import os
import re
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
NORMALIZED_VERSION = re.compile(
    r"(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*((a|b|rc)(0|[1-9][0-9]*))?(\.post(0|[1-9][0-9]*))?"
)
"""A release version as PyPI writes it (PEP 440): no development or local part."""


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

    `version` must already be in the normalized form the build gives it (such as
    0.2.0rc1, not 0.2.0-rc1), so the files, the tag and the checks all use the
    same string. From main it is `version`, which must not be on PyPI, on
    TestPyPI or tagged already: an index never accepts the same version twice.
    From anywhere else it is a development version unique to the run, so a trial
    on TestPyPI doesn't use up the version the real release needs there.
    """
    if not NORMALIZED_VERSION.fullmatch(version):
        raise ReleaseRefused(
            f"the version {version!r} in pyproject.toml isn't in the normalized "
            "form PyPI uses, such as 0.2.0 or 0.2.0rc1. Write it that way, with "
            "no development or local part."
        )
    if ref != MAIN_REF:
        development = f"{version}.dev{run_number}"
        if on_testpypi(development):
            raise ReleaseRefused(
                f"{PACKAGE} {development} is already on TestPyPI. Start a new "
                "run: re-running all jobs reuses the run number."
            )
        return development
    bump = (
        "Bump the version in pyproject.toml in a pull request, then run the "
        "release again."
    )
    if on_pypi(version):
        raise ReleaseRefused(
            f"{PACKAGE} {version} is already on PyPI. {bump} If that release's "
            "tag or GitHub release is missing, re-run the failed jobs of the run "
            "that published it instead."
        )
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
