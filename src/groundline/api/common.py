"""What every check endpoint shares: the request, the common response parts, and
how a failed check becomes an HTTP error.

Every finished check answers 200, flagged or not (ADR 0008). No response or log
line ever holds text from the source or summary: error messages are either
written to be safe, or left to `groundline.api.errors`, which never repeats one.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated

import anthropic
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from groundline.core import (
    ModelClient,
    ModelError,
    ReplyError,
    SetupError,
    StageError,
    summarize_usage,
)
from groundline.evaluators.models import AlarmOutput
from groundline.evaluators.report import usage_text
from groundline.result import EvaluationResult

logger = logging.getLogger("groundline.api")

MAX_CHARS = 200_000
"""The longest source or summary accepted, far longer than any consultation, so
one request can't spend dollars on a huge input."""

# Stage failures caused by the model or Anthropic's API, which answer 502. Our
# own two are written to be safe to show; the SDK's can quote the request, so
# only its type is shown. Any other failure is a bug of ours, and its message
# may quote the source or summary.
UPSTREAM_CAUSES = (ReplyError, ModelError, anthropic.APIError)

FAILURE_RESPONSES: dict[int | str, dict[str, str]] = {
    502: {"description": "The model or Anthropic's API failed."},
    503: {"description": "The Anthropic API key is missing or rejected."},
}
"""The statuses a model failure can cause, beyond 200 and the 422 FastAPI
documents. Check routes use `PROTECTED_RESPONSES`, which adds authentication's."""

PROTECTED_RESPONSES: dict[int | str, dict[str, str]] = {
    **FAILURE_RESPONSES,
    401: {"description": "The API key is missing or invalid."},
    503: {
        "description": "Authentication is unavailable, or the Anthropic API key is "
        "missing or rejected."
    },
}
"""`FAILURE_RESPONSES` for a route that also depends on `require_api_key`."""


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


class CheckRequest(ApiModel):
    """The body of every check: a source and the summary to check against it."""

    source: Annotated[str, Field(min_length=1, max_length=MAX_CHARS)]
    """The original material, such as a consultation transcript."""
    summary: Annotated[str, Field(min_length=1, max_length=MAX_CHARS)]
    """The AI-written summary to check, such as a clinical note."""

    @field_validator("source", "summary")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class Alarm(ApiModel):
    """The flag decision. Each endpoint subclasses it to say what its score is."""

    score: float
    """The highest confidence among the summary's critical problems; 0 if there
    are none."""
    threshold: float
    """The alarm score at or above which the summary is flagged."""
    level: str
    """The threshold level used, such as strict."""


class Usage(ApiModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


def model_client() -> ModelClient | None:
    """The model client to use; None means the default Anthropic client.
    Tests override this dependency with a scripted fake."""
    return None


def alarm_of[A: Alarm](alarm: AlarmOutput, model: type[A]) -> A:
    """The alarm in the endpoint's own `Alarm` model."""
    return model(score=alarm.score, threshold=alarm.threshold, level=alarm.level)


def usage_of(result: EvaluationResult) -> Usage:
    usage = result.run.usage
    return Usage(
        calls=usage.calls,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
    )


@contextmanager
def check_failures() -> Generator[None]:
    """Turn what stops a check into the HTTP error it deserves: 503 when the
    Anthropic API key is missing or rejected or the settings can't be read, and
    502 when the model or Anthropic's API fails. Anything else is a bug of ours:
    it passes through, and the app-wide handler answers 500."""
    try:
        yield
    except SetupError as error:
        logger.warning("can't check: %s", error)
        raise HTTPException(503, detail=str(error)) from error
    except StageError as error:
        spent = _spent(error)
        cause = type(error.__cause__).__name__
        logger.warning("stage '%s' failed (%s)%s", error.step, cause, spent)
        if not isinstance(error.__cause__, UPSTREAM_CAUSES):
            raise
        raise HTTPException(502, detail=f"{_failure(error)}{spent}") from error


def _failure(error: StageError) -> str:
    """What went wrong upstream. An SDK error is named, never quoted."""
    cause = error.__cause__
    if not isinstance(cause, anthropic.APIError):
        return str(error)
    if isinstance(cause, anthropic.APIStatusError):
        what = f"answered {cause.status_code}"
    else:
        what = "failed"
    name = type(cause).__name__
    return f"stage '{error.step}' failed: the Anthropic API {what} ({name})"


def _spent(error: StageError) -> str:
    """What a failed check had cost, if it made any calls."""
    if not error.calls:
        return ""
    return f". Cost before stopping: {usage_text(summarize_usage(error.calls))}"
