"""What the SDK package installs, and how its generator is pinned (ALL-155).

scripts/sdk-install-check installs the built package to check the same thing
against a real install.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[1]
GENERATOR = "openapi-python-client"


def load(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text())


def names(requirements: list[str]) -> set[str]:
    return {
        re.split(r"[<>=!~\[ ;]", requirement, maxsplit=1)[0]
        for requirement in requirements
    }


def test_the_package_is_groundline_client_for_python_313_and_later():
    project = load(PACKAGE / "pyproject.toml")["project"]
    assert project["name"] == "groundline-client"
    assert project["requires-python"] == ">=3.13"


def test_installing_the_sdk_asks_only_for_what_the_generated_code_uses():
    project = load(PACKAGE / "pyproject.toml")["project"]
    assert names(project["dependencies"]) == {"attrs", "httpx2"}
    assert "optional-dependencies" not in project


def test_the_generator_is_pinned_to_one_version_and_not_a_runtime_dependency():
    config = load(PACKAGE / "pyproject.toml")
    assert GENERATOR not in names(config["project"]["dependencies"])
    pins = [r for r in config["dependency-groups"]["dev"] if r.startswith(GENERATOR)]
    assert len(pins) == 1
    assert re.fullmatch(rf"{GENERATOR}==\d+\.\d+\.\d+", pins[0])


def test_the_package_is_a_member_of_the_uv_workspace():
    workspace = load(ROOT / "pyproject.toml")["tool"]["uv"]["workspace"]
    assert "packages/*" in workspace["members"]
