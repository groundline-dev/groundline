"""The release workflow's safeguards: who can start it, what it may do, and that
PyPI gets exactly the files TestPyPI was checked with."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
"""An action pinned to a full commit SHA, as in ci.yml."""
PUBLISH_ACTION = "pypa/gh-action-pypi-publish@"


@pytest.fixture(scope="module")
def workflow() -> dict[Any, Any]:
    return yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())


def jobs(workflow: dict[Any, Any]) -> dict[str, Any]:
    return workflow["jobs"]


def steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return job["steps"]


def runs(job: dict[str, Any]) -> str:
    return "\n".join(step.get("run", "") for step in steps(job))


def test_only_a_maintainer_starting_it_by_hand_runs_it(workflow: dict[Any, Any]):
    # PyYAML reads the key `on` as True.
    triggers: dict[str, Any] = workflow[True]

    assert set(triggers) == {"workflow_dispatch"}, triggers
    assert workflow["permissions"] == {"contents": "read"}


def test_pypi_waits_for_approval_and_only_runs_from_main(workflow: dict[Any, Any]):
    pypi = jobs(workflow)["pypi"]

    assert pypi["environment"]["name"] == "pypi"
    assert pypi["if"] == "github.ref == 'refs/heads/main'", pypi["if"]
    assert "verify" in pypi["needs"], pypi["needs"]


def test_only_the_upload_jobs_can_get_a_publishing_token(workflow: dict[Any, Any]):
    with_token = {
        name
        for name, job in jobs(workflow).items()
        if job.get("permissions", {}).get("id-token") == "write"
    }

    assert with_token == {"testpypi", "pypi"}


def test_only_the_release_job_can_write_to_the_repository(workflow: dict[Any, Any]):
    writers = {
        name
        for name, job in jobs(workflow).items()
        if job.get("permissions", {}).get("contents") == "write"
    }

    assert writers == {"release"}


def test_each_index_gets_its_own_environment(workflow: dict[Any, Any]):
    testpypi = jobs(workflow)["testpypi"]
    upload = next(
        step
        for step in steps(testpypi)
        if step.get("uses", "").startswith(PUBLISH_ACTION)
    )

    assert testpypi["environment"]["name"] == "testpypi"
    assert upload["with"]["repository-url"] == "https://test.pypi.org/legacy/"


def test_pypi_uploads_the_files_that_were_checked(workflow: dict[Any, Any]):
    # Building again could produce different files from the ones installed and
    # checked from TestPyPI.
    pypi = jobs(workflow)["pypi"]

    assert "uv build" not in runs(pypi)
    assert any(
        step.get("uses", "").startswith("actions/download-artifact@")
        for step in steps(pypi)
    )


def test_the_build_checks_what_it_built_and_verify_checks_testpypi(
    workflow: dict[Any, Any],
):
    build, verify = jobs(workflow)["build"], jobs(workflow)["verify"]

    assert "scripts/release_version.py" in runs(build)
    assert "scripts/core-install-check --dist" in runs(build)
    assert "scripts/core-install-check --from-index" in runs(verify)


def test_every_action_is_pinned_to_a_commit(workflow: dict[Any, Any]):
    unpinned = [
        step["uses"]
        for job in jobs(workflow).values()
        for step in steps(job)
        if "uses" in step and not PINNED.match(step["uses"])
    ]

    assert unpinned == []


def test_the_tag_comes_from_the_version_script(workflow: dict[Any, Any]):
    # One place decides the tag's name, so the version check and the release
    # agree on which tag means "already released".
    release = jobs(workflow)["release"]

    assert 'gh release create "$TAG"' in runs(release)
    assert "groundline-core-v$VERSION" not in runs(release)


def test_whether_it_is_a_prerelease_comes_from_the_version_script(
    workflow: dict[Any, Any],
):
    # scripts/release_version.py holds the version rules and their tests.
    release = jobs(workflow)["release"]

    assert jobs(workflow)["build"]["outputs"]["prerelease"] == (
        "${{ steps.version.outputs.prerelease }}"
    )
    assert '"$PRERELEASE" = true' in runs(release)
    assert "=~" not in runs(release)
    assert (
        jobs(workflow)["build"]["outputs"]["tag"] == "${{ steps.version.outputs.tag }}"
    )


def test_release_jobs_build_without_the_shared_cache(workflow: dict[Any, Any]):
    # Other workflows write the uv cache, so a release must not build from it.
    cached = [
        name
        for name, job in jobs(workflow).items()
        for step in steps(job)
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
        and step.get("with", {}).get("enable-cache") is not False
    ]

    assert cached == []


def test_a_partly_failed_pypi_upload_can_be_finished(workflow: dict[Any, Any]):
    upload = next(
        step
        for step in steps(jobs(workflow)["pypi"])
        if step.get("uses", "").startswith(PUBLISH_ACTION)
    )

    assert upload.get("with", {}).get("skip-existing") is True
