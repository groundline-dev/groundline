"""The release workflows' safeguards: who can start them, what they may do, and
that PyPI gets exactly the files TestPyPI was checked with. Every check runs
against the library's workflow and the SDK's."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
"""An action pinned to a full commit SHA, as in ci.yml."""
PUBLISH_ACTION = "pypa/gh-action-pypi-publish@"


RELEASES = {
    "release.yml": ("groundline-core", "scripts/core-install-check"),
    "release-sdk.yml": ("groundline-client", "scripts/sdk-install-check"),
}
"""Each release workflow, with the package it publishes and the script that
checks that package's built files."""


@pytest.fixture(params=RELEASES, scope="module")
def release_file(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(scope="module")
def package(release_file: str) -> str:
    return RELEASES[release_file][0]


@pytest.fixture(scope="module")
def install_check(release_file: str) -> str:
    return RELEASES[release_file][1]


@pytest.fixture(scope="module")
def workflow(release_file: str) -> dict[Any, Any]:
    return yaml.safe_load((ROOT / ".github/workflows" / release_file).read_text())


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
    workflow: dict[Any, Any], install_check: str
):
    build, verify = jobs(workflow)["build"], jobs(workflow)["verify"]

    assert "scripts/release_version.py" in runs(build)
    assert f"{install_check} --dist" in runs(build)
    assert f"{install_check} --from-index" in runs(verify)


def test_every_action_is_pinned_to_a_commit(workflow: dict[Any, Any]):
    unpinned = [
        step["uses"]
        for job in jobs(workflow).values()
        for step in steps(job)
        if "uses" in step and not PINNED.match(step["uses"])
    ]

    assert unpinned == []


def test_the_tag_comes_from_the_version_script(workflow: dict[Any, Any], package: str):
    # One place decides the tag's name, so the version check and the release
    # agree on which tag means "already released".
    release = jobs(workflow)["release"]

    assert 'gh release create "$TAG"' in runs(release)
    assert f"{package}-v$VERSION" not in runs(release)
    assert (
        jobs(workflow)["build"]["outputs"]["tag"] == "${{ steps.version.outputs.tag }}"
    )


def test_whether_it_is_a_prerelease_comes_from_the_version_script(
    workflow: dict[Any, Any],
):
    # scripts/release_version.py holds the version rules and their tests.
    release = jobs(workflow)["release"]

    assert jobs(workflow)["build"]["outputs"]["prerelease"] == (
        "${{ steps.version.outputs.prerelease }}"
    )
    assert 'case "$PRERELEASE" in' in runs(release)
    assert "=~" not in runs(release)


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


def workflow_text(release_file: str) -> str:
    return (ROOT / ".github/workflows" / release_file).read_text()


def test_each_workflow_publishes_only_its_own_package(release_file: str, package: str):
    other = next(name for name, _ in RELEASES.values() if name != package)
    text = workflow_text(release_file)

    assert package in text
    assert other not in text, f"{release_file} names {other}"
    assert other.replace("-", "_") not in text, f"{release_file} names {other}"


def test_the_version_script_is_told_which_package_to_check(
    workflow: dict[Any, Any], package: str
):
    build = runs(jobs(workflow)["build"])

    assert f"scripts/release_version.py {package}" in build


def test_only_the_library_takes_the_latest_badge(
    workflow: dict[Any, Any], package: str
):
    # Without --latest, the newest release by date is the repository's Latest, so
    # the two workflows would take the badge, and `releases/latest`, from each other.
    release = runs(jobs(workflow)["release"])

    if package == "groundline-client":
        assert "--latest=false" in release
    else:
        assert "--latest" not in release


def test_the_upload_pages_and_wait_name_the_package(
    workflow: dict[Any, Any], package: str
):
    file_stem = package.replace("-", "_")

    for name in ("testpypi", "pypi"):
        url = jobs(workflow)[name]["environment"]["url"]
        assert f"/project/{package}/" in url, url
    wait = runs(jobs(workflow)["verify"])
    assert f"{file_stem}-$VERSION.tar.gz" in wait
    assert f"{file_stem}-$VERSION-py3-none-any.whl" in wait
    assert f"/simple/{package}/" in wait


def test_release_notes_start_from_the_previous_release_of_the_same_package(
    workflow: dict[Any, Any], package: str
):
    release = runs(jobs(workflow)["release"])

    assert f'startswith("{package}-v")' in release
    assert f'--title "{package} $VERSION"' in release


def test_the_two_workflows_never_wait_on_each_other():
    # The library and the SDK each get their own queue.
    groups = {
        file: yaml.safe_load(workflow_text(file))["concurrency"]["group"]
        for file in RELEASES
    }

    assert len(set(groups.values())) == len(groups), groups


def test_both_workflows_use_the_same_two_environments():
    # One set of reviewers approves both packages, and the PyPI publishers name
    # these environments.
    used = {
        file: {
            job["environment"]["name"]
            for job in yaml.safe_load(workflow_text(file))["jobs"].values()
            if "environment" in job
        }
        for file in RELEASES
    }

    assert used == {file: {"testpypi", "pypi"} for file in RELEASES}


def test_the_sdk_is_built_and_versioned_as_its_own_package():
    workflow = yaml.safe_load(workflow_text("release-sdk.yml"))
    build = runs(jobs(workflow)["build"])

    assert "uv build --package groundline-client" in build
    assert "uv version --package groundline-client" in build


# What tells a maintainer, or an agent, how to release.

DEVELOPMENT = (ROOT / "DEVELOPMENT.md").read_text()


def test_agents_md_warns_about_every_release_workflow():
    agents = (ROOT / "AGENTS.md").read_text()

    for file in RELEASES:
        assert f".github/workflows/{file}" in agents, file


def test_the_section_agents_md_points_to_exists_in_development_md():
    agents = (ROOT / "AGENTS.md").read_text()
    match = re.search(r'DEVELOPMENT\.md, "([^"]+)"', agents)

    assert match, "AGENTS.md no longer says which DEVELOPMENT.md section to read"
    assert re.search(rf"^#+ {re.escape(match.group(1))}$", DEVELOPMENT, re.M), match[1]


def test_development_md_lists_each_workflow_with_its_package_and_tag_prefix():
    for file, (package, _) in RELEASES.items():
        assert f"`{file}`" in DEVELOPMENT, file
        assert f"`{package}-v" in DEVELOPMENT, f"no tag prefix for {package}"


def test_the_one_time_setup_adds_a_pending_publisher_for_each_package():
    setup = DEVELOPMENT.split("### One-time setup", 1)[1].split("\n## ", 1)[0]

    for file, (package, _) in RELEASES.items():
        assert f"`{package}`" in setup, package
        assert f"`{file}`" in setup, file
