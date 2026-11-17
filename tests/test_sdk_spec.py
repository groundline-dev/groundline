"""The committed OpenAPI file and the folder of generated SDK code (ALL-155).

The SDK is generated from packages/groundline-client/openapi.json. These tests
check what only the server's side can see: that the file is the app's current
description, and that the generated folder is marked so nobody edits it by hand.
`make sdk-check` checks that the generated code matches the file.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "groundline-client"
SPEC = PACKAGE / "openapi.json"
GENERATED = PACKAGE / "src" / "groundline_client" / "_generated"


def load_export_script():
    path = ROOT / "scripts" / "export_openapi.py"
    spec = importlib.util.spec_from_file_location("export_openapi", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_openapi_file_is_the_apps_current_description():
    committed = json.loads(SPEC.read_text())
    # The description with the default limits, as the export builds it, so the
    # developer's own limits (shell or .env) can't change the result.
    current = load_export_script().default_spec()
    assert committed == current, "run make generate-sdk and commit the result"


def test_the_generated_folder_says_not_to_edit_it_by_hand():
    notice = (GENERATED / "README.md").read_text()
    assert "make generate-sdk" in notice
    assert "by hand" in notice


def test_git_treats_the_generated_folder_and_file_as_generated():
    lines = (ROOT / ".gitattributes").read_text().splitlines()
    folder = GENERATED.relative_to(ROOT).as_posix()
    assert f"{folder}/** linguist-generated=true" in lines
    assert f"{SPEC.relative_to(ROOT).as_posix()} linguist-generated=true" in lines


def test_local_limits_do_not_change_the_exported_description(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GROUNDLINE_MAX_SUMMARY_CHARS=4321\n")
    monkeypatch.setenv("GROUNDLINE_MAX_SOURCE_CHARS", "1234")
    spec = json.dumps(load_export_script().default_spec())
    assert "1234" not in spec
    assert "4321" not in spec
