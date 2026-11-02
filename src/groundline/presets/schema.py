"""The JSON schema editors use to check pipeline specs, such as presets.

Each built-in preset's first line points editors at it:

    # yaml-language-server: $schema=pipeline.schema.json

A spec elsewhere can point at the copy on GitHub. The schema is generated from
the built-in implementations' settings models. After adding an implementation
or changing its settings, regenerate it with:

    uv run python -m groundline.presets.schema
"""

import json
from pathlib import Path
from typing import Any

from groundline.core.spec import spec_json_schema
from groundline.evaluate import default_registry

SCHEMA_PATH = Path(__file__).parent / "pipeline.schema.json"


def spec_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **spec_json_schema(default_registry()),
    }


if __name__ == "__main__":
    SCHEMA_PATH.write_text(json.dumps(spec_schema(), indent=2) + "\n")
    print(f"wrote {SCHEMA_PATH}")
