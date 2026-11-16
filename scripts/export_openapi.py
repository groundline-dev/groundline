"""Write the API's OpenAPI description to the SDK's openapi.json (ALL-155).

The SDK is generated from that file. Run it through scripts/generate-sdk (make
generate-sdk), which also regenerates the SDK.
"""

import json
from pathlib import Path

from groundline_core.app import app

SPEC = Path(__file__).resolve().parents[1] / "packages/groundline-client/openapi.json"


def main() -> None:
    # Two-space indent and a final newline, so a change shows as a small diff.
    SPEC.write_text(json.dumps(app.openapi(), indent=2) + "\n")
    print(f"wrote {SPEC.relative_to(SPEC.parents[2])}")


if __name__ == "__main__":
    main()
