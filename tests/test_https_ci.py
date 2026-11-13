"""Run the workflow's real input predicate against controlled changed paths."""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "changed,expected",
    [
        ("scripts/https_check.py", True),
        ("tests/https_smoke.py", True),
        ("infra/https/Caddyfile", True),
        ("compose.https.yaml", True),
        ("README.md", False),
        ("src/groundline_core/evaluate.py", False),
    ],
)
def test_https_workflow_input_detection(tmp_path: Path, changed: str, expected: bool):
    workflow: dict[str, Any] = yaml.safe_load(
        (ROOT / ".github/workflows/ci.yml").read_text()
    )
    script = next(
        step["run"]
        for step in workflow["jobs"]["https"]["steps"]
        if step.get("id") == "changes"
    )
    git = tmp_path / "git"
    git.write_text(
        '#!/bin/sh\ncase "$1" in\n'
        "cat-file) exit 0 ;;\n"
        'diff) printf "%s\\n" "$CHANGED_PATH" ;;\n'
        "*) exit 99 ;;\nesac\n"
    )
    git.chmod(0o755)
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env=dict(
            os.environ,
            PATH=f"{tmp_path}:{os.environ['PATH']}",
            CHANGED_PATH=changed,
            BASE_SHA="baseline",
            GITHUB_OUTPUT=str(output),
        ),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert output.exists() == expected
    if expected:
        assert output.read_text() == "run=true\n"
