"""What every check endpoint shares: the request, the common response parts, and
how a failed check becomes an HTTP error.

Every finished check answers 200, flagged or not (ADR 0008). No response or log
line ever holds text from the source or summary: error messages are either
written to be safe, or left to `groundline.api.errors`, which never repeats one.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated, Any

import anthropic
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from groundline.api.errors import (
    AUTHENTICATION_UNAVAILABLE,
    INVALID_API_KEY,
    UNEXPECTED,
)
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


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


class ErrorBody(ApiModel):
    """The body of every error but a 422."""

    detail: str


class Problem(ApiModel):
    """One thing wrong with a request. It never repeats the rejected value."""

    loc: list[str | int]
    """Where the problem is, such as ["body", "source"]."""
    msg: str
    type: str


class InvalidRequestBody(ApiModel):
    """The body of a 422."""

    detail: list[Problem]


def _error(
    model: type[ApiModel], description: str, **examples: object
) -> dict[str, Any]:
    """An error status's entry in a route's `responses`, with its examples."""
    return {
        "model": model,
        "description": description,
        "content": {
            "application/json": {
                "examples": {name: {"value": value} for name, value in examples.items()}
            }
        },
    }


ANTHROPIC_KEY_MISSING = {
    "detail": "ANTHROPIC_API_KEY is not set.\n"
    "  Looked in: the environment, and /home/groundline/.env (not found)\n"
    "  Fix: export ANTHROPIC_API_KEY=..., or copy .env.example to .env and add "
    "it there."
}

FAILURE_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: _error(
        ErrorBody,
        "The body isn't UTF-8 text.",
        not_utf8={"detail": "There was an error parsing the body"},
    ),
    422: _error(
        InvalidRequestBody,
        "The request is unusable: broken JSON, a blank or too-long text, or an "
        "unexpected field. The response never repeats the rejected value.",
        blank_and_extra={
            "detail": [
                {
                    "loc": ["body", "source"],
                    "msg": "String should have at least 1 character",
                    "type": "string_too_short",
                },
                {
                    "loc": ["body"],
                    "msg": "Extra inputs are not permitted",
                    "type": "extra_forbidden",
                },
            ]
        },
    ),
    500: _error(
        ErrorBody,
        "An unexpected error, a bug in groundline. The details are in the server log.",
        unexpected={"detail": UNEXPECTED},
    ),
    502: _error(
        ErrorBody,
        "The model or Anthropic's API failed. Retrying later may work.",
        omissions_unusable_reply={
            "detail": "stage 'extract' failed: unusable reply after 1 retry: no "
            "JSON object in the reply (the reply was 8 characters). Cost before "
            "stopping: $0.0043 · 2 calls · 1,054 input / 4 output tokens"
        },
        commissions_anthropic_error={
            "detail": "stage 'commissions_check' failed: the Anthropic API "
            "answered 529 (OverloadedError)"
        },
    ),
    503: _error(
        ErrorBody,
        "The Anthropic API key is missing or rejected.",
        anthropic_key_missing=ANTHROPIC_KEY_MISSING,
    ),
}
"""The statuses a check can answer besides 200, with examples. FastAPI's own 422
entry is replaced, since groundline's 422 leaves out the rejected value. Check
routes use `PROTECTED_RESPONSES`, which adds authentication's."""

PROTECTED_RESPONSES: dict[int | str, dict[str, Any]] = {
    **FAILURE_RESPONSES,
    401: {
        **_error(
            ErrorBody,
            "The API key is missing or invalid.",
            invalid_key={"detail": INVALID_API_KEY},
        ),
        "headers": {
            "WWW-Authenticate": {
                "description": "Always `Bearer`.",
                "schema": {"type": "string"},
            }
        },
    },
    503: _error(
        ErrorBody,
        "Authentication is unavailable, or the Anthropic API key is missing or "
        "rejected.",
        authentication_unavailable={"detail": AUTHENTICATION_UNAVAILABLE},
        anthropic_key_missing=ANTHROPIC_KEY_MISSING,
    ),
}
"""`FAILURE_RESPONSES` for a route that also depends on `require_api_key`."""

_EXAMPLE_SOURCE = (
    "Doctor: Any allergies? Patient: Penicillin, it gives me a rash. Doctor: "
    "Your knee is sprained. Take Motrin 600 mg every 6 hours for a week, and "
    "come back if it isn't better in two weeks."
)
_EXAMPLE_SUMMARY = (
    "Sprained knee. Allergic to penicillin. Motrin 800 mg every 6 hours for a "
    "week. Return if not improving in two weeks."
)
"""An invented consultation for the docs' examples. FastAPI leaves out null
values when it puts an example in the docs, so no example holds one."""


class CheckRequest(ApiModel):
    """The body of every check: a source and the summary to check against it."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"source": _EXAMPLE_SOURCE, "summary": _EXAMPLE_SUMMARY}]
        }
    )

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
