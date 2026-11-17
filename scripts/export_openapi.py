"""Write the API's OpenAPI description to the SDK's openapi.json (ALL-155).

The SDK is generated from that file. Run it through scripts/generate-sdk (make
generate-sdk), which also regenerates the SDK.

The description always uses the API's default request limits. The API reads
GROUNDLINE_MAX_* from the environment or a .env file when it is imported, and
those values show up in the description, so a developer's own limits would
otherwise end up in the committed file.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SPEC = Path(__file__).resolve().parents[1] / "packages/groundline-client/openapi.json"

PRINT_SPEC = (
    "import json; from groundline_core.app import app; print(json.dumps(app.openapi()))"
)


def default_spec() -> dict[str, Any]:
    """The app's OpenAPI description as a deployment with no settings builds it.

    The app is imported in a new process, with no GROUNDLINE_* variables and an
    empty working folder, so neither the shell nor a .env file can change it.
    """
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("GROUNDLINE_")
    }
    with tempfile.TemporaryDirectory() as empty:
        result = subprocess.run(
            [sys.executable, "-c", PRINT_SPEC],
            cwd=empty,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        sys.exit(f"export_openapi: building the spec failed:\n{result.stderr}")
    return json.loads(result.stdout)


def main() -> None:
    # An optional output file, so a check can export without touching the repo.
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else SPEC
    # Two-space indent and a final newline, so a change shows as a small diff.
    target.write_text(json.dumps(default_spec(), indent=2) + "\n")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
