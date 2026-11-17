"""The committed OpenAPI file and the folder of generated SDK code (ALL-155).

The SDK is generated from packages/groundline-client/openapi.json. These tests
check what only the server's side can see: that the file is the app's current
description, and that the generated folder is marked so nobody edits it by hand.
`make sdk-check` checks that the generated code matches the file.
"""

import importlib.util
import json
import re
from pathlib import Path
from typing import Any, cast

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
    # The description as the export builds it: the default limits, so the
    # developer's own limits (shell or .env) can't change the result.
    current = load_export_script().sdk_spec()
    assert committed == current, "run make generate-sdk and commit the result"


def differences(raw: Any, stripped: Any, path: str = "") -> list[str]:
    """Where the two descriptions differ, as paths."""
    if isinstance(raw, dict) and isinstance(stripped, dict):
        found: list[str] = []
        keys = set(cast(dict[str, Any], raw)) | set(cast(dict[str, Any], stripped))
        for key in sorted(keys):
            where = f"{path}/{key}"
            if key not in stripped:
                found.append(where)
            elif key not in raw:
                found.append(f"{where} (added)")
            else:
                found.extend(differences(raw[key], stripped[key], where))
        return found
    return [] if raw == stripped else [path]


def test_the_sdk_spec_differs_from_the_apps_only_by_the_request_defaults():
    script = load_export_script()
    changed = differences(script.default_spec(), script.sdk_spec())
    assert changed, "the preset default should be left out of the SDK's copy"
    assert all(
        re.fullmatch(r"/components/schemas/\w+/properties/\w+/default", path)
        for path in changed
    ), changed
    assert "/components/schemas/CommissionsRequest/properties/preset/default" in changed
    assert not any("Response" in path for path in changed)


def test_the_generated_request_leaves_the_preset_to_the_server():
    from groundline_client._generated.models import CommissionsRequest

    body = CommissionsRequest.from_dict({"source": "a", "summary": "b"})
    assert "preset" not in body.to_dict()


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
    # All three limits that can reach the spec: one in .env, one in the shell, one
    # in the shell in lowercase (the settings ignore case).
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GROUNDLINE_MAX_SUMMARY_CHARS=4321\n")
    monkeypatch.setenv("GROUNDLINE_MAX_SOURCE_CHARS", "1234")
    monkeypatch.setenv("groundline_max_body_bytes", "5678")
    spec = json.dumps(load_export_script().default_spec())
    for value in ("1234", "4321", "5678"):
        assert value not in spec
