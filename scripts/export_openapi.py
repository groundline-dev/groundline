"""Write the API's OpenAPI description to the SDK's openapi.json (ALL-155).

The SDK is generated from that file. Run it through scripts/generate-sdk (make
generate-sdk), which also regenerates the SDK.

Two things are fixed so the file doesn't depend on who ran it:
- It always uses the API's default request limits. The API reads
  GROUNDLINE_MAX_* from the environment or a .env file when it is imported, and
  those values show up in the description, so a developer's own limits would
  otherwise end up in the committed file.
- It leaves out the defaults of request fields, so the SDK doesn't pin them (see
  without_request_defaults).
"""

import copy
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

    The app is imported in a new process, with no GROUNDLINE_* variables (in any
    case, since the settings ignore case) and an empty working folder, so neither
    the shell nor a .env file can change it.
    """
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("GROUNDLINE_")
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


def without_request_defaults(spec: dict[str, Any]) -> dict[str, Any]:
    """A copy of the description with the `default` of each request field removed.

    The generator turns a default into a value the SDK always sends, so a caller
    on an old SDK would keep sending the old default after the API changes it.
    Without it, the generated field is left out unless the caller sets it, and
    the server chooses. Only the SDK's copy is changed: the API's own /docs still
    shows the defaults, and responses are left alone.
    """
    result = copy.deepcopy(spec)
    for methods in result["paths"].values():
        for operation in methods.values():
            content = operation.get("requestBody", {}).get("content", {})
            for media in content.values():
                reference = media.get("schema", {}).get("$ref")
                if reference is None:
                    continue
                schema = result["components"]["schemas"][reference.rsplit("/", 1)[-1]]
                for field in schema.get("properties", {}).values():
                    field.pop("default", None)
    return result


def sdk_spec() -> dict[str, Any]:
    """The description the SDK is generated from: the defaults-only spec, without
    the request defaults."""
    return without_request_defaults(default_spec())


def main() -> None:
    # An optional output file, so a check can export without touching the repo.
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else SPEC
    # Two-space indent and a final newline, so a change shows as a small diff.
    target.write_text(json.dumps(sdk_spec(), indent=2) + "\n")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
