"""Check what pyproject.toml installs, and under which names (ALL-160, ADR 0016).

- A plain install of groundline-core gets only what the pipelines need.
- The server extra adds the API, and the cli extra adds the groundline-core
  command and key management. Both need the database.
- The dev group pulls in both extras, so a plain `uv sync` still sets up
  everything for development.
- The package is installed as groundline-core, imported as groundline_core and
  run as groundline-core, so nothing clashes with the unrelated `groundline`
  project on PyPI.

scripts/core-install-check installs the built package to check the same lists
against a real install.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from groundline_core.extras import CLI_PACKAGES, SERVER_PACKAGES

ROOT = Path(__file__).resolve().parents[1]

CORE = {"anthropic", "pydantic-settings", "pyyaml"}
DATABASE = {"alembic", "psycopg", "sqlalchemy"}
SERVER = {"fastapi"} | DATABASE
CLI = {"platformdirs", "typer"} | DATABASE

REQUIREMENT = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?")
"""The name of a requirement such as `fastapi[standard]>=0.162.0`, and its extras."""


@pytest.fixture(scope="module")
def pyproject() -> dict[str, Any]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def parse(requirement: str) -> tuple[str, set[str]]:
    """A requirement's normalized name, and the extras it asks for."""
    match = REQUIREMENT.match(requirement)
    assert match, f"not a requirement: {requirement!r}"
    name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
    extras = {extra.strip() for extra in (match.group(2) or "").split(",") if extra}
    return name, extras


def names(requirements: list[str]) -> set[str]:
    return {parse(requirement)[0] for requirement in requirements}


def test_a_plain_install_gets_only_what_the_pipelines_need(pyproject: dict[str, Any]):
    assert names(pyproject["project"]["dependencies"]) == CORE


@pytest.mark.parametrize(("extra", "expected"), [("server", SERVER), ("cli", CLI)])
def test_each_extra_adds_its_own_packages_and_the_database(
    pyproject: dict[str, Any], extra: str, expected: set[str]
):
    extras = pyproject["project"].get("optional-dependencies", {})
    assert names(extras.get(extra, [])) == expected


@pytest.mark.parametrize(
    ("extra", "known"), [("server", SERVER_PACKAGES), ("cli", CLI_PACKAGES)]
)
def test_the_missing_extra_messages_know_every_package_in_the_extra(
    pyproject: dict[str, Any], extra: str, known: frozenset[str]
):
    # extras.py names the extra to install only for a package it knows, so a
    # package added to an extra must be added there too. Each package in these
    # extras is imported under the name it is installed as.
    assert names(pyproject["project"]["optional-dependencies"][extra]) <= known


def test_the_dev_group_installs_both_extras(pyproject: dict[str, Any]):
    dev = [parse(requirement) for requirement in pyproject["dependency-groups"]["dev"]]
    assert ("groundline-core", {"server", "cli"}) in dev


def test_the_package_is_installed_and_run_as_groundline_core(
    pyproject: dict[str, Any],
):
    assert pyproject["project"]["name"] == "groundline-core"
    assert pyproject["project"]["scripts"] == {
        "groundline-core": "groundline_core.command:main"
    }
    assert pyproject["tool"]["fastapi"]["entrypoint"] == "groundline_core.app:app"


def test_the_package_is_imported_as_groundline_core():
    assert (ROOT / "src" / "groundline_core" / "__init__.py").is_file()
    assert not (ROOT / "src" / "groundline").exists()
