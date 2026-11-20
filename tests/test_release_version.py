"""Which version the release workflows publish, and when they refuse."""

import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from scripts.release_version import (
    PACKAGES,
    ReleaseRefused,
    has_version,
    is_prerelease,
    project_version,
    release_version,
    tag_for,
)

ROOT = Path(__file__).resolve().parents[1]
MAIN = "refs/heads/main"
BRANCH = "refs/heads/try-the-release"

every_package = pytest.mark.parametrize(
    "package", ["groundline-core", "groundline-client"]
)


def never(_: str) -> bool:
    return False


def must_not_be_asked(_: str) -> bool:
    raise AssertionError("a run from a branch shouldn't look anything up")


@every_package
def test_main_publishes_the_version_in_pyproject(package: str):
    chosen = release_version(
        package=package,
        version="0.1.0",
        ref=MAIN,
        run_number=7,
        on_pypi=never,
        on_testpypi=never,
        tagged=never,
    )

    assert chosen == "0.1.0"


@every_package
def test_main_refuses_a_version_already_on_pypi(package: str):
    with pytest.raises(ReleaseRefused, match=rf"{package} 0\.1\.0 is already on PyPI"):
        release_version(
            package=package,
            version="0.1.0",
            ref=MAIN,
            run_number=7,
            on_pypi=lambda version: version == "0.1.0",
            on_testpypi=never,
            tagged=never,
        )


@every_package
def test_main_refuses_a_version_left_on_testpypi_by_a_stopped_run(package: str):
    # A run that uploaded to TestPyPI but stopped before PyPI (a failed check or
    # a rejected approval) leaves the version there for good.
    with pytest.raises(
        ReleaseRefused, match=rf"{package} 0\.1\.0 is already on TestPyPI"
    ):
        release_version(
            package=package,
            version="0.1.0",
            ref=MAIN,
            run_number=7,
            on_pypi=never,
            on_testpypi=lambda version: version == "0.1.0",
            tagged=never,
        )


@every_package
def test_main_refuses_a_version_that_was_already_tagged(package: str):
    with pytest.raises(ReleaseRefused, match=f"{package}-v0.1.0 already exists"):
        release_version(
            package=package,
            version="0.1.0",
            ref=MAIN,
            run_number=7,
            on_pypi=never,
            on_testpypi=never,
            tagged=lambda tag: tag == f"{package}-v0.1.0",
        )


def test_a_tag_of_the_other_package_does_not_count_as_released():
    # Both packages release from one repository and start at the same version.
    chosen = release_version(
        package="groundline-client",
        version="0.1.0",
        ref=MAIN,
        run_number=7,
        on_pypi=never,
        on_testpypi=never,
        tagged=lambda tag: tag == "groundline-core-v0.1.0",
    )

    assert chosen == "0.1.0"


@every_package
def test_another_branch_publishes_a_development_version(package: str):
    # TestPyPI never accepts the same version twice, so runs from a branch must
    # not use up the version the real release will need there.
    chosen = release_version(
        package=package,
        version="0.1.0",
        ref=BRANCH,
        run_number=42,
        on_pypi=must_not_be_asked,
        on_testpypi=never,
        tagged=must_not_be_asked,
    )

    assert chosen == "0.1.0.dev42"


@every_package
def test_a_rerun_of_a_branch_run_is_refused_before_uploading(package: str):
    # Re-running all jobs keeps the run number, so the development version is
    # already on TestPyPI.
    with pytest.raises(ReleaseRefused, match=rf"{package} 0\.1\.0\.dev42 .*new run"):
        release_version(
            package=package,
            version="0.1.0",
            ref=BRANCH,
            run_number=42,
            on_pypi=must_not_be_asked,
            on_testpypi=lambda version: version == "0.1.0.dev42",
            tagged=must_not_be_asked,
        )


@pytest.mark.parametrize(
    "version", ["0.2.0-rc1", "0.2.0.dev1", "0.2.0+local", "v0.2.0"]
)
def test_a_version_not_in_normalized_form_is_refused(version: str):
    # The build normalizes the version in the file names, so anything else
    # would make the tag and the checks disagree with the uploaded files.
    with pytest.raises(ReleaseRefused, match="normalized form"):
        release_version(
            package="groundline-client",
            version=version,
            ref=MAIN,
            run_number=7,
            on_pypi=never,
            on_testpypi=never,
            tagged=never,
        )


def test_a_prerelease_in_normalized_form_is_accepted():
    chosen = release_version(
        package="groundline-client",
        version="0.2.0rc1",
        ref=MAIN,
        run_number=7,
        on_pypi=never,
        on_testpypi=never,
        tagged=never,
    )

    assert chosen == "0.2.0rc1"


def test_a_release_is_tagged_with_the_package_name():
    assert tag_for("groundline-core", "0.1.0") == "groundline-core-v0.1.0"
    assert tag_for("groundline-client", "0.1.0") == "groundline-client-v0.1.0"


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.2.0", False),
        ("0.2.0.post1", False),
        ("0.2.0a1", True),
        ("0.2.0rc1.post1", True),
    ],
)
def test_alpha_beta_and_release_candidates_are_prereleases(
    version: str, expected: bool
):
    assert is_prerelease(version) is expected


def test_the_script_knows_the_library_and_the_sdk():
    assert set(PACKAGES) == {"groundline-core", "groundline-client"}


@every_package
def test_each_package_reads_its_own_version_from_its_own_pyproject(package: str):
    expected = tomllib.loads((ROOT / PACKAGES[package]).read_text())["project"][
        "version"
    ]

    assert project_version(package, ROOT) == expected


def test_the_sdk_version_comes_from_the_sdks_pyproject_not_the_libraries(
    tmp_path: Path,
):
    (tmp_path / "packages/groundline-client").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
    (tmp_path / "packages/groundline-client/pyproject.toml").write_text(
        '[project]\nversion = "0.3.0"\n'
    )

    assert project_version("groundline-client", tmp_path) == "0.3.0"
    assert project_version("groundline-core", tmp_path) == "1.0.0"


class Reply:
    def __enter__(self) -> "Reply":
        return self

    def __exit__(self, *_: Any) -> None:
        return None


@every_package
def test_an_index_lookup_asks_for_that_packages_version(
    package: str, monkeypatch: pytest.MonkeyPatch
):
    asked: list[str] = []

    def urlopen(url: str, timeout: int) -> Reply:
        asked.append(url)
        return Reply()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    assert has_version("https://pypi.org", package, "0.1.0") is True
    assert asked == [f"https://pypi.org/pypi/{package}/0.1.0/json"]


def test_an_index_that_has_no_such_version_says_so(monkeypatch: pytest.MonkeyPatch):
    def urlopen(url: str, timeout: int) -> Reply:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    assert has_version("https://test.pypi.org", "groundline-client", "9.9.9") is False


def test_an_index_error_other_than_not_found_stops_the_run(
    monkeypatch: pytest.MonkeyPatch,
):
    # A 500 must not read as "not published yet", or a release could go ahead
    # without the duplicate check.
    def urlopen(url: str, timeout: int) -> Reply:
        raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    with pytest.raises(urllib.error.HTTPError):
        has_version("https://pypi.org", "groundline-client", "0.1.0")


def run_script(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/release_version.py"), *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env={"GITHUB_REF": BRANCH, "GITHUB_RUN_NUMBER": "1"},
    )


def test_an_unknown_package_is_refused_and_the_known_ones_are_listed():
    result = run_script("groundline")

    assert result.returncode != 0
    assert "groundline-core" in result.stderr and "groundline-client" in result.stderr
    assert result.stdout == ""
