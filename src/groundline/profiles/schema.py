"""The JSON schema editors use to check `profile.yaml` files.

Each built-in profile's first line points editors at it:

    # yaml-language-server: $schema=../profile.schema.json

The schema is generated from the profile model. After changing the model,
regenerate it with:

    uv run python -m groundline.profiles.schema
"""

import json
from pathlib import Path
from typing import Any

from groundline.core.profile import profile_file_schema

SCHEMA_PATH = Path(__file__).parent / "profile.schema.json"


def profile_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **profile_file_schema(),
    }


if __name__ == "__main__":
    SCHEMA_PATH.write_text(json.dumps(profile_schema(), indent=2) + "\n")
    print(f"wrote {SCHEMA_PATH}")
