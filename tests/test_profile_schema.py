"""The JSON schema editors use to check `profile.yaml` files."""

import json

from groundline.profiles import BUILTIN_DIR, builtin_profiles
from groundline.profiles.schema import SCHEMA_PATH, profile_schema


def test_the_committed_schema_matches_the_profile_model():
    committed = json.loads(SCHEMA_PATH.read_text())

    assert committed == profile_schema(), (
        "profile.schema.json is out of date: run "
        "`uv run python -m groundline.profiles.schema`"
    )


def test_the_schema_rejects_unknown_fields():
    schema = profile_schema()

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"name", "placeholders", "texts"}


def test_every_built_in_profile_points_editors_at_the_schema():
    for name in builtin_profiles():
        manifest = BUILTIN_DIR / name / "profile.yaml"
        first_line = manifest.read_text().splitlines()[0]

        assert first_line == "# yaml-language-server: $schema=../profile.schema.json"
        assert (manifest.parent / "../profile.schema.json").resolve() == SCHEMA_PATH
