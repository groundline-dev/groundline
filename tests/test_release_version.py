"""Which version the release workflow publishes, and when it refuses."""

import pytest

from scripts.release_version import ReleaseRefused, release_version, tag_for

MAIN = "refs/heads/main"


def never(_: str) -> bool:
    return False


def must_not_be_asked(_: str) -> bool:
    raise AssertionError("a run from a branch shouldn't look anything up")


def test_main_publishes_the_version_in_pyproject():
    chosen = release_version(
        version="0.1.0",
        ref=MAIN,
        run_number=7,
        on_pypi=never,
        on_testpypi=never,
        tagged=never,
    )

    assert chosen == "0.1.0"


def test_main_refuses_a_version_already_on_pypi():
    with pytest.raises(ReleaseRefused, match=r"0\.1\.0 is already on PyPI"):
        release_version(
            version="0.1.0",
            ref=MAIN,
            run_number=7,
            on_pypi=lambda version: version == "0.1.0",
            on_testpypi=never,
            tagged=never,
        )


def test_main_refuses_a_version_left_on_testpypi_by_a_stopped_run():
    # A run that uploaded to TestPyPI but stopped before PyPI (a failed check or
    # a rejected approval) leaves the version there for good.
    with pytest.raises(ReleaseRefused, match=r"0\.1\.0 is already on TestPyPI"):
        release_version(
            version="0.1.0",
            ref=MAIN,
            run_number=7,
            on_pypi=never,
            on_testpypi=lambda version: version == "0.1.0",
            tagged=never,
        )


def test_main_refuses_a_version_that_was_already_tagged():
    with pytest.raises(ReleaseRefused, match="groundline-core-v0.1.0 already exists"):
        release_version(
            version="0.1.0",
            ref=MAIN,
            run_number=7,
            on_pypi=never,
            on_testpypi=never,
            tagged=lambda tag: tag == "groundline-core-v0.1.0",
        )


BRANCH = "refs/heads/try-the-release"


def test_another_branch_publishes_a_development_version():
    # TestPyPI never accepts the same version twice, so runs from a branch must
    # not use up the version the real release will need there.
    chosen = release_version(
        version="0.1.0",
        ref=BRANCH,
        run_number=42,
        on_pypi=must_not_be_asked,
        on_testpypi=never,
        tagged=must_not_be_asked,
    )

    assert chosen == "0.1.0.dev42"


def test_a_rerun_of_a_branch_run_is_refused_before_uploading():
    # Re-running all jobs keeps the run number, so the development version is
    # already on TestPyPI.
    with pytest.raises(ReleaseRefused, match="Start a new run"):
        release_version(
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
            version=version,
            ref=MAIN,
            run_number=7,
            on_pypi=never,
            on_testpypi=never,
            tagged=never,
        )


def test_a_prerelease_in_normalized_form_is_accepted():
    chosen = release_version(
        version="0.2.0rc1",
        ref=MAIN,
        run_number=7,
        on_pypi=never,
        on_testpypi=never,
        tagged=never,
    )

    assert chosen == "0.2.0rc1"


def test_a_release_is_tagged_with_the_package_name():
    assert tag_for("0.1.0") == "groundline-core-v0.1.0"
