"""What the SDK package installs, and how its generator is pinned.

scripts/sdk-install-check installs the built package to check the same thing
against a real install.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_DIR.parents[1]
GENERATOR = "openapi-python-client"


def read_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text())


def requirement_names(requirements: list[str]) -> set[str]:
    """The names of the requirements, normalized the way pip compares them."""
    names: set[str] = set()
    for requirement in requirements:
        name = re.split(r"[<>=!~\[ ;]", requirement, maxsplit=1)[0]
        names.add(re.sub(r"[-_.]+", "-", name).lower())
    return names


def test_the_package_is_groundline_client_for_python_313_and_later():
    project = read_toml(PACKAGE_DIR / "pyproject.toml")["project"]
    assert project["name"] == "groundline-client"
    assert project["requires-python"] == ">=3.13"


def test_installing_the_sdk_asks_only_for_what_the_generated_code_uses():
    project = read_toml(PACKAGE_DIR / "pyproject.toml")["project"]
    assert requirement_names(project["dependencies"]) == {"attrs", "httpx2"}
    assert "optional-dependencies" not in project


def test_the_generator_is_pinned_to_one_version_and_not_a_runtime_dependency():
    config = read_toml(PACKAGE_DIR / "pyproject.toml")
    assert GENERATOR not in requirement_names(config["project"]["dependencies"])
    pins = [r for r in config["dependency-groups"]["dev"] if r.startswith(GENERATOR)]
    assert len(pins) == 1
    assert re.fullmatch(rf"{GENERATOR}==\d+\.\d+\.\d+", pins[0])


def test_the_package_is_a_member_of_the_uv_workspace():
    workspace = read_toml(ROOT / "pyproject.toml")["tool"]["uv"]["workspace"]
    assert "packages/*" in workspace["members"]


def test_the_package_carries_the_repos_license_and_its_own_short_notice():
    project = read_toml(PACKAGE_DIR / "pyproject.toml")["project"]
    assert project["license-files"] == ["LICENSE", "NOTICE"]
    assert (PACKAGE_DIR / "LICENSE").read_text() == (ROOT / "LICENSE").read_text(), (
        "LICENSE differs from the repo's: copy it again"
    )
    # Not a copy of the repo's NOTICE, which also credits test fixtures that the
    # wheel doesn't hold. It has to start with the same name and copyright lines.
    notice = (PACKAGE_DIR / "NOTICE").read_text()
    repo_head = "".join((ROOT / "NOTICE").read_text().splitlines(keepends=True)[:2])
    assert notice.startswith(repo_head), "NOTICE must start with the repo's two lines"
    assert "fixtures" not in notice


# What PyPI shows on the project page, kept the same as groundline-core's.

REPOSITORY = "https://github.com/groundline-dev/groundline"
README = (PACKAGE_DIR / "README.md").read_text()


def project() -> dict[str, Any]:
    return read_toml(PACKAGE_DIR / "pyproject.toml")["project"]


def test_the_description_says_what_the_sdk_does():
    description = project()["description"]

    assert description != "Python SDK for the groundline API.", description
    assert "SDK" in description, description
    assert "summar" in description.lower(), description


def test_the_classifiers_are_typed_alpha_and_not_medical():
    classifiers = project()["classifiers"]

    assert "Development Status :: 3 - Alpha" in classifiers, classifiers
    assert "Typing :: Typed" in classifiers, classifiers
    assert "Programming Language :: Python :: 3.13" in classifiers, classifiers
    assert "License :: OSI Approved :: Apache Software License" not in classifiers, (
        "the license comes from license-files; a license classifier is deprecated"
    )
    # A medical topic would describe a clinical intended use, which the package
    # doesn't have.
    assert [c for c in classifiers if "Medical" in c] == []


def test_the_project_links_point_to_the_repository():
    urls = project()["urls"]

    assert set(urls) == {"Homepage", "Source", "Issues"}, urls
    for name, url in urls.items():
        assert url.startswith(REPOSITORY), (name, url)


def test_only_the_shared_address_is_published():
    # Author entries are published with every version and can't be changed
    # afterwards, so they carry names only.
    assert [a for a in project()["authors"] if "email" in a] == []
    assert project()["maintainers"] == [
        {"name": "groundline developers", "email": "groundlinedevs@gmail.com"}
    ]


def test_the_sdk_names_the_same_authors_as_the_library():
    library = read_toml(ROOT / "pyproject.toml")["project"]

    assert project()["authors"] == library["authors"]


def version_parts() -> tuple[int, int]:
    major, minor, *_ = project()["version"].split(".")
    return int(major), int(minor)


def section(heading: str) -> str:
    """The README text under `## heading`, up to the next `## `."""
    match = re.search(
        rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", README, re.M | re.S
    )
    assert match, f"the README has no '## {heading}' section"
    return match.group(1)


def test_the_readme_gives_the_version_policy_and_a_pin_for_this_minor():
    major, minor = version_parts()
    text = section("Versions")

    assert "minor" in text and "patch" in text, text
    # The pin is only the advice before 1.0, when a minor release may break callers.
    if major == 0:
        assert f"groundline-client>=0.{minor},<0.{minor + 1}" in text, text


def compatibility_rows() -> dict[str, list[str]]:
    """Each row of the Compatibility table, by its first cell."""
    rows: dict[str, list[str]] = {}
    for line in section("Compatibility").splitlines():
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if line.startswith("|") and re.fullmatch(r"\d+\.\d+\.x", cells[0]):
            rows[cells[0]] = cells[1:]
    return rows


def test_the_compatibility_table_has_a_row_for_this_version_and_names_its_api_version():
    major, minor = version_parts()
    rows = compatibility_rows()

    assert f"{major}.{minor}.x" in rows, f"no row for {major}.{minor}.x in {rows}"
    assert "/v1" in rows[f"{major}.{minor}.x"][0], rows


def test_every_row_names_the_library_release_the_sdk_was_generated_from():
    rows = compatibility_rows()

    assert rows, "the Compatibility table has no rows"
    for sdk_version, cells in rows.items():
        assert re.search(r"groundline-core \d+\.\d+\.\d+", cells[1]), (
            sdk_version,
            cells,
        )


def test_the_sdk_says_it_versions_apart_from_the_library():
    text = section("Versions")

    assert "groundline-core" in text and "own version" in text, text
