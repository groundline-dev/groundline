"""What every evaluation starts from: a source and a summary of it."""

from pydantic import BaseModel, ConfigDict


class Inputs(BaseModel):
    """A source (such as a consultation transcript) and a summary of it (such as a
    clinical note). Domain wording belongs in profiles, not here."""

    model_config = ConfigDict(frozen=True)

    source: str
    summary: str
