"""Domain profiles: what matters in a domain, kept in configuration, not code.

A profile is a folder with a `profile.yaml` and the texts it lists: the prompts
for each step and the severity rubric. Stages refer to texts by name, so a new
domain (finance, say) needs a new profile folder but no code changes.

    name: clinical
    placeholders: {source: transcript, summary: note}
    texts:
      extract_coarse_v1: prompts/extract_coarse_v1.txt
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ProfileError(ValueError):
    pass


class Placeholders(BaseModel):
    """The placeholder names a profile's prompts use for the inputs, such as
    `{transcript}` for the source and `{note}` for the summary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(
        default="source",
        description="The placeholder the prompts use for the source, without "
        "braces: `transcript` means the prompts say {transcript}.",
    )
    summary: str = Field(
        default="summary",
        description="The placeholder the prompts use for the summary, without "
        "braces: `note` means the prompts say {note}.",
    )


class DomainProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str = ""
    placeholders: Placeholders = Placeholders()
    texts: dict[str, str]
    """Text contents by name."""


class _ProfileFile(BaseModel):
    """The shape of `profile.yaml`: like a profile, but texts are file paths."""

    model_config = ConfigDict(extra="forbid", title="groundline domain profile")

    name: str = Field(description="The profile's name, such as `clinical`.")
    description: str = Field(default="", description="What the profile is for.")
    placeholders: Placeholders = Field(
        description="The placeholders the prompts use for the source and summary."
    )
    texts: dict[str, str] = Field(
        description="The prompts and rubrics the pipeline stages can use: a name "
        "for each, and its file path relative to this folder."
    )


def profile_file_schema() -> dict[str, Any]:
    """The JSON schema of `profile.yaml`, for editors to check profiles with."""
    return _ProfileFile.model_json_schema()


def load_profile(folder: str | Path) -> DomainProfile:
    """Load a profile folder, reading every text it lists."""
    folder = Path(folder)
    manifest = folder / "profile.yaml"
    if not manifest.exists():
        raise ProfileError(f"{folder}: no profile.yaml")
    try:
        spec = _ProfileFile.model_validate(yaml.safe_load(manifest.read_text()))
    except (yaml.YAMLError, ValidationError) as error:
        raise ProfileError(f"{manifest}: {error}") from error

    texts: dict[str, str] = {}
    for name, relative in spec.texts.items():
        path = folder / relative
        if not path.is_file():
            raise ProfileError(f"{manifest}: text '{name}' file {relative} not found")
        texts[name] = path.read_text()

    return DomainProfile(
        name=spec.name,
        description=spec.description,
        placeholders=spec.placeholders,
        texts=texts,
    )
