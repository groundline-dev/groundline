"""The errors groundline raises, sorted by what the caller can do about them.

`GroundlineError` is the base. `SetupError` means fix the setup before trying
again, `InvalidInputError` means the source or summary can't be checked, and
`ModelFailureError` means the model didn't give a usable answer in time, so
trying again later may work. Anything else that comes out of a check, such as
an exception from the caller's own model client, is passed on as it was raised
(ADR 0020).

No message from a check names a file's folder, a key, or text from the
source or summary, so it is safe to log or show to anyone. Messages do quote
names written in a spec or profile, such as an unknown step, so anything shaped
like an Anthropic or groundline API key is replaced with `<redacted key>` in
case one was pasted there. Only those two shapes are recognized: another kind
of secret pasted into a spec or profile would still be quoted.

When an error comes from another library's error, it is raised `from None`, so
a traceback doesn't print the original, whose text can quote a file, a setting
or the request. The original is still on `__context__` for debugging in code.

A file's full path is in `SetupError.path` instead, which the CLI prints on a
`File:` line of its own.
"""

import re
from pathlib import Path
from typing import Literal

from groundline_core._core.calls import CallRecord

KEY_SHAPES = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}|gl_[A-Za-z0-9_-]{20,}")
"""An Anthropic API key, or a groundline API key (see `api_keys.py`)."""


def redact_keys(text: str) -> str:
    """`text` with anything shaped like an API key replaced."""
    return KEY_SHAPES.sub("<redacted key>", text)


class GroundlineError(Exception):
    """The base of every error groundline raises on purpose. Catch it to handle
    a setup error, invalid input or a model failure together."""

    def __init__(self, *args: object) -> None:
        # Redacted here, so `str`, `repr` and `args` are all clean. A subclass
        # that adds text in its own `__str__` redacts that text itself.
        super().__init__(
            *(redact_keys(arg) if isinstance(arg, str) else arg for arg in args)
        )


class SetupError(GroundlineError):
    """Something about the setup stops the check, such as an invalid pipeline
    spec, a missing or rejected Anthropic API key, a model the Anthropic API
    doesn't have, or settings that can't be read. Trying again won't help until
    the setup is fixed."""

    def __init__(self, message: str, *, path: str | Path | None = None) -> None:
        super().__init__(message)
        self.path = Path(path).absolute() if path is not None else None
        """The absolute path of the file at fault, such as the `.env` file that
        was read; None when no file is involved. The message names the file
        only by its name."""


def file_line(path: Path | None) -> str:
    """The line the CLI prints and the API logs after an error's message: the
    full path of the file at fault, and whether it exists. Empty when no file is
    involved. The message itself names the file only by its name."""
    if path is None:
        return ""
    try:
        missing = "" if path.exists() else " (not found)"
    except OSError:
        # A folder on the way that can't be searched: say nothing rather than
        # fail while reporting another error.
        missing = ""
    return f"\n  File: {path}{missing}"


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
        if self.step:
            message = redact_keys(f"stage '{self.step}' failed: {message}")
        return message


class ModelTimeoutError(ModelFailureError):
    """The check's time limit ran out while waiting for the model, or a model
    call ran out of time: one attempt took too long, or its retries did. A
    call that used up its attempts on errors is a plain `ModelFailureError`.

    Interrupted requests may still be billed, so the cost groundline records
    can be lower than the provider's bill."""

    def __init__(self, reason: Literal["check", "attempt", "retry"] = "check") -> None:
        super().__init__("time limit expired while waiting for the model")
        self.reason = reason
