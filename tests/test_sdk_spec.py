"""The committed OpenAPI file and the folder of generated SDK code (ALL-155).

The SDK is generated from packages/groundline-client/openapi.json. These tests
check what only the server's side can see: that the file is the app's current
description, and that the generated folder is marked so nobody edits it by hand.
`make sdk-check` checks that the generated code matches the file.
"""

import json
from pathlib import Path

from groundline_core.app import app

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "groundline-client"
SPEC = PACKAGE / "openapi.json"
GENERATED = PACKAGE / "src" / "groundline_client" / "_generated"


def test_the_committed_openapi_file_is_the_apps_current_description():
    committed = json.loads(SPEC.read_text())
    # A round trip through JSON, so the comparison sees what a file would hold.
    current = json.loads(json.dumps(app.openapi()))
    assert committed == current, "run make generate-sdk and commit the result"


def test_the_generated_folder_says_not_to_edit_it_by_hand():
    notice = (GENERATED / "README.md").read_text()
    assert "make generate-sdk" in notice
    assert "by hand" in notice


def test_git_treats_the_generated_folder_as_generated():
    path = GENERATED.relative_to(ROOT).as_posix()
    lines = (ROOT / ".gitattributes").read_text().splitlines()
    assert f"{path}/** linguist-generated=true" in lines
