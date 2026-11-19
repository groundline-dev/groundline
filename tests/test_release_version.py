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
        version="0.1.0", ref=MAIN, run_number=7, published=never, tagged=never
    )

    assert chosen == "0.1.0"


def test_main_refuses_a_version_already_on_pypi():
    with pytest.raises(ReleaseRefused, match=r"0\.1\.0 is already on PyPI"):
        release_version(
            version="0.1.0",
            ref=MAIN,
            run_number=7,
            published=lambda version: version == "0.1.0",
            tagged=never,
        )


def test_main_refuses_a_version_that_was_already_tagged():
    with pytest.raises(ReleaseRefused, match="groundline-core-v0.1.0 already exists"):
        release_version(
            version="0.1.0",
            ref=MAIN,
            run_number=7,
            published=never,
            tagged=lambda tag: tag == "groundline-core-v0.1.0",
        )


def test_another_branch_publishes_a_development_version():
    # TestPyPI never accepts the same version twice, so runs from a branch must
    # not use up the version the real release will need there.
    chosen = release_version(
        version="0.1.0",
        ref="refs/heads/all-164-build-and-publish",
        run_number=42,
        published=must_not_be_asked,
        tagged=must_not_be_asked,
    )

    assert chosen == "0.1.0.dev42"


def test_a_release_is_tagged_with_the_package_name():
    assert tag_for("0.1.0") == "groundline-core-v0.1.0"
