"""The errors groundline raises, sorted by what the caller can do about them.

`GroundlineError` is the base. `SetupError` means fix the setup before trying
again, `InvalidInputError` means the source or summary can't be checked, and
`ModelFailureError` means the model didn't give a usable answer in time, so
trying again later may work. Anything else that comes out of a check, such as
an exception from the caller's own model client, is passed on as it was raised
(ADR 0020).

No message names a file's folder, a key, or text from the source or summary,
so a message is safe to log or show to anyone. A file's full path is in
`SetupError.path` instead.
"""

from pathlib import Path
from typing import Literal

from groundline_core._core.calls import CallRecord


class GroundlineError(Exception):
    """The base of every error groundline raises on purpose. Catch it to handle
    a setup error, invalid input or a model failure together."""


class SetupError(GroundlineError):
    """Something about the setup stops the check, such as an invalid pipeline
    spec, a missing or rejected Anthropic API key, or settings that can't be
    read. Trying again won't help until the setup is fixed."""

    def __init__(self, message: str, *, path: str | Path | None = None) -> None:
        super().__init__(message)
        self.path = Path(path).absolute() if path is not None else None
        """The absolute path of the file at fault, such as the `.env` file that
        was read; None when no file is involved. The message names the file
        only by its name."""


class APIKeyRejectedError(SetupError):
    """The Anthropic API rejected the Anthropic API key, or the key's workspace
    can't use the model. `path` is the file the key came from, if any."""


class InvalidInputError(GroundlineError, ValueError):
    """The source or summary can't be checked: it isn't text, or it's empty or
    only whitespace. Raised before any model is called."""


class ModelFailureError(GroundlineError):
    """The model didn't give a usable answer: Anthropic's API kept failing after
    its retries, the model declined, or its reply stayed unusable after a
    request for correction. Trying again later may work.

    A custom model client can raise it to report its own model's failure."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.step: str | None = None
        """The step whose stage failed, such as `extract`; None when the whole
        check ran out of time between stages."""
        self.calls: tuple[CallRecord, ...] = ()
        # Undocumented: every recorded model call before the check stopped,
        # for the cost line the CLI and API print.

    def __str__(self) -> str:
        message = super().__str__()
        return f"stage '{self.step}' failed: {message}" if self.step else message


class ModelTimeoutError(ModelFailureError):
    """The check's time limit ran out while waiting for the model, or a model
    call used up its attempts or its time.

    Interrupted requests may still be billed, so the cost groundline records
    can be lower than the provider's bill."""

    def __init__(self, reason: Literal["check", "attempt", "retry"] = "check") -> None:
        super().__init__("time limit expired while waiting for the model")
        self.reason = reason
