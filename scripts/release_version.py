"""Choose the version a release workflow publishes.

Each release workflow's build job runs it with the name of its package (the
library, groundline-core, when none is given) and appends what it prints to
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

LIBRARY = "groundline-core"
PACKAGES = {
    LIBRARY: Path("pyproject.toml"),
    "groundline-client": Path("packages/groundline-client/pyproject.toml"),
}
"""Each package released from this repository, with the pyproject.toml that holds
its version, relative to the repository's root."""
MAIN_REF = "refs/heads/main"
PYPI = "https://pypi.org"
TESTPYPI = "https://test.pypi.org"
NORMALIZED_VERSION = re.compile(
    r"(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*((a|b|rc)(0|[1-9][0-9]*))?(\.post(0|[1-9][0-9]*))?"
)
"""A release version as PyPI writes it (PEP 440): no development or local part."""


class ReleaseRefused(Exception):
    """The run must stop before anything is uploaded."""


def is_prerelease(version: str) -> bool:
    """Whether a normalized version is a pre-release (an alpha, beta or release
    candidate, including a post-release of one), which mustn't become the
    repository's latest release."""
    match = NORMALIZED_VERSION.fullmatch(version)
    return bool(match and match.group(4))


def tag_for(package: str, version: str) -> str:
    """The git tag for a release, named after the package so both packages can be
    tagged in the same repository."""
    return f"{package}-v{version}"


def release_version(
    *,
    package: str,
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
            f"the version {version!r} in {PACKAGES[package]} isn't in the normalized "
            "form PyPI uses, such as 0.2.0 or 0.2.0rc1. Write it that way, with "
            "no development or local part."
        )
    if ref != MAIN_REF:
        development = f"{version}.dev{run_number}"
        if on_testpypi(development):
            raise ReleaseRefused(
                f"{package} {development} is already on TestPyPI. Start a new "
                "run: re-running all jobs reuses the run number."
            )
        return development
    bump = (
        f"Bump the version in {PACKAGES[package]} in a pull request, then run "
        "the release again."
    )
    if on_pypi(version):
        raise ReleaseRefused(
            f"{package} {version} is already on PyPI. {bump} If that release's "
            "tag or GitHub release is missing, re-run the failed jobs of the run "
            "that published it instead."
        )
    if on_testpypi(version):
        raise ReleaseRefused(
            f"{package} {version} is already on TestPyPI from an earlier run that "
            f"stopped before PyPI, and TestPyPI won't take it again. {bump}"
        )
    if tagged(tag_for(package, version)):
        raise ReleaseRefused(
            f"the tag {tag_for(package, version)} already exists, but {version} "
            "isn't on PyPI. Check whether an earlier release stopped partway."
        )
    return version


def has_version(index: str, package: str, version: str) -> bool:
    """Whether the package index at `index` (such as https://pypi.org) lists the
    version. Only a 404 means no: any other error stops the run, so a failed
    lookup can't pass for "not published yet"."""
    url = f"{index}/pypi/{package}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30):
            return True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise


def project_version(package: str, root: Path) -> str:
    """The version in the package's pyproject.toml, under the repository `root`."""
    pyproject = tomllib.loads((root / PACKAGES[package]).read_text())
    return pyproject["project"]["version"]


def _tag_exists(tag: str) -> bool:
    # The workflow checks out every tag (fetch-depth: 0).
    rev_parse = subprocess.run(
        ["git", "rev-parse", "--quiet", "--verify", f"refs/tags/{tag}"],
        capture_output=True,
        check=False,
    )
    return rev_parse.returncode == 0


def main() -> None:
    """Print `version=…`, `tag=…` and `prerelease=…` for $GITHUB_OUTPUT, or stop
    with a reason. The package is the first argument, and defaults to the
    library, whose workflow predates the argument."""
    package = sys.argv[1] if len(sys.argv) > 1 else LIBRARY
    if package not in PACKAGES:
        sys.exit(
            f"unknown package {package!r}; the packages are: {', '.join(PACKAGES)}"
        )
    try:
        chosen = release_version(
            package=package,
            version=project_version(package, Path()),
            ref=os.environ["GITHUB_REF"],
            run_number=int(os.environ["GITHUB_RUN_NUMBER"]),
            on_pypi=lambda version: has_version(PYPI, package, version),
            on_testpypi=lambda version: has_version(TESTPYPI, package, version),
            tagged=_tag_exists,
        )
    except ReleaseRefused as refusal:
        sys.exit(f"release refused: {refusal}")
    print(f"version={chosen}")
    print(f"tag={tag_for(package, chosen)}")
    print(f"prerelease={str(is_prerelease(chosen)).lower()}")


if __name__ == "__main__":
    main()
