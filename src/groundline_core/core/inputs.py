"""What every evaluation starts from: a source and a summary of it."""

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict

InputName = Literal["source", "summary"]


class Inputs(BaseModel):
    """A source (such as a consultation transcript) and a summary of it (such as a
    clinical note). Domain wording belongs in profiles, not here."""

    model_config = ConfigDict(frozen=True)

    source: str
    summary: str

    def text(self, name: InputName) -> str:
        return self.source if name == "source" else self.summary

    def sha256(self, name: InputName) -> str:
        """Identifies an input in cache keys without storing its text there."""
        return hashlib.sha256(self.text(name).encode()).hexdigest()
