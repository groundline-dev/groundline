"""What every check endpoint shares: the request, the common response parts, and
how a failed check becomes an HTTP error.

Every finished check answers 200, flagged or not (ADR 0008). No response or log
line ever holds text from the source or summary: error messages are either
written to be safe, or left to `groundline.api.errors`, which never repeats one.
"""

import logging
from collections.abc import AsyncGenerator, Generator
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
    CallRecord,
    ModelClient,
    ModelError,
    PipelineSpec,
    ReplyError,
    SetupError,
    StageError,
    build_pipeline,
    summarize_usage,
)
from groundline.core.deadline import check_deadline
from groundline.core.errors import ModelTimeoutError
from groundline.evaluate import default_registry, model_users
from groundline.evaluators.models import AlarmOutput
from groundline.evaluators.report import usage_text
from groundline.profiles import load_builtin_profile
from groundline.result import EvaluationResult
from groundline.settings import MissingAPIKeyError, read_settings

logger = logging.getLogger("groundline.api")

MAX_CHARS = 200_000
"""The longest source or summary accepted, far longer than any consultation, so
one request can't spend dollars on a huge input."""

# A stage can fail because the model timed out (504) or because the model or
# Anthropic's API failed in another way (502). These exceptions cover both cases.
# Unexpected stage failures are handled by the app and return 500.
#
# Groundline's model error messages are safe to include in the response.
# Anthropic SDK messages may contain source or summary text, so the response
# includes only the SDK error's type and HTTP status, when available.
UPSTREAM_CAUSES = (ReplyError, ModelError, ModelTimeoutError, anthropic.APIError)


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
    model: type[ApiModel], description: str, **examples: tuple[str, object]
) -> dict[str, Any]:
    """An error status's entry in a route's `responses`. Each example is a
    label for the docs' picker and the body."""
    return {
        "model": model,
        "description": description,
        "content": {
            "application/json": {
                "examples": {
                    name: {"summary": summary, "value": value}
                    for name, (summary, value) in examples.items()
                }
            }
        },
    }


FAILURE_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: _error(
        ErrorBody,
        "The body isn't valid UTF-8 text. Bytes that begin like UTF-16 or UTF-32 "
        "text are read as that, and answer 422 if they aren't JSON.",
        not_utf8=("Not UTF-8", {"detail": "There was an error parsing the body"}),
    ),
    422: _error(
        InvalidRequestBody,
        "The request is unusable: broken JSON, not a JSON object, a blank or "
        "too-long text, or an unexpected field. The response never repeats the "
        "rejected value.",
        blank_and_extra=(
            "A blank source and an extra field",
            {
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
    ),
    500: _error(
        ErrorBody,
        "An unexpected error, a bug in groundline. The details are in the server log.",
        unexpected=("Unexpected error", {"detail": UNEXPECTED}),
    ),
}
"""The statuses any check can answer besides 200, 502, 503 and 504, with examples.
FastAPI's 422 entry is replaced to leave out the rejected value.
Check routes use `check_responses`, which adds authentication's 401 and the
502, 503 and 504 responses, with examples."""

PROTECTED_RESPONSES: dict[int | str, dict[str, Any]] = {
    **FAILURE_RESPONSES,
    401: {
        **_error(
            ErrorBody,
            "The API key is missing or invalid.",
            invalid_key=("Invalid or missing key", {"detail": INVALID_API_KEY}),
        ),
        "headers": {
            "WWW-Authenticate": {
                "description": "Always `Bearer`.",
                "schema": {"type": "string"},
            }
        },
    },
}
"""`FAILURE_RESPONSES` plus the 401 of a route that depends on `require_api_key`."""


def check_responses(spec: PipelineSpec) -> dict[int | str, dict[str, Any]]:
    """Describe the errors a check endpoint can return in the API docs.

    Start with the request and authentication errors in `PROTECTED_RESPONSES`.
    Add 502 for model or provider failures, 503 for configuration or
    authentication problems, and 504 when a time limit expires.

    Tailor the examples to this pipeline: use its first model stage and pricing
    for the 502 examples, and name the stages that need an Anthropic API key
    in the missing-key 503 example.
    """
    profile = load_builtin_profile(spec.profile) if spec.profile else None
    pipeline = build_pipeline(spec, default_registry(), profile)
    stage = next(s for s in pipeline.stages if s.enabled and s.implementation.models())
    # Illustrative token counts: a first prompt and its retry, each answered
    # with "not json".
    call = CallRecord(
        step=stage.step,
        model=stage.implementation.models()[0],
        input_tokens=800,
        output_tokens=2,
        seconds=1.0,
    )
    cost = usage_text(summarize_usage([call, call]))
    failed = f"stage '{stage.step}' failed"
    upstream = _error(
        ErrorBody,
        "The model returned an error or unusable output after the permitted "
        "outage retries or correction request. A Retry-After that cannot fit "
        "the remaining allowance also stops with 502 before deadline expiry.",
        unusable_reply=(
            "Unusable reply",
            {
                "detail": f"{failed}: The model’s reply remained unusable "
                f"after a request for correction: no JSON object in the reply "
                f"(the reply was 8 characters). Recorded cost before stopping: "
                f"{cost}"
            },
        ),
        anthropic_error=(
            "Anthropic API error",
            {"detail": f"{failed}: the Anthropic API answered 529 (OverloadedError)"},
        ),
    )
    unavailable = _error(
        ErrorBody,
        "Authentication is unavailable, the Anthropic API key is missing or "
        "rejected, or the settings can't be read.",
        authentication_unavailable=(
            "Authentication unavailable",
            {"detail": AUTHENTICATION_UNAVAILABLE},
        ),
        anthropic_key_missing=(
            "Anthropic API key not set",
            {
                "detail": MissingAPIKeyError(
                    "ANTHROPIC_API_KEY", model_users(pipeline)
                ).api_message()
            },
        ),
    )
    timeout = _error(
        ErrorBody,
        "The check, model attempt, or outage retry allowance expired. "
        "Recorded usage may exclude charges for interrupted provider requests.",
        model_timeout=(
            "Model time limit",
            {"detail": "time limit expired while waiting for the model"},
        ),
    )
    return PROTECTED_RESPONSES | {502: upstream, 503: unavailable, 504: timeout}


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


async def begin_check() -> AsyncGenerator[None]:
    with check_failures():
        seconds = read_settings().check_timeout_seconds
    with check_deadline(seconds):
        yield


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
    502 when the model or Anthropic's API fails, and 504 for owned model/check
    time limits. Anything else is a bug of ours:
    it passes through, and the app-wide handler answers 500."""
    try:
        yield
    except SetupError as error:
        logger.warning("can't check: %s", error.api_message())
        raise HTTPException(503, detail=error.api_message()) from error
    except ModelTimeoutError as error:
        spent = _spent(error)
        logger.warning("check timed out (reason=%s)%s", error.reason, spent)
        raise HTTPException(504, detail=f"{error}{spent}") from None
    except StageError as error:
        spent = _spent(error)
        cause = error.__cause__
        if isinstance(cause, (ModelTimeoutError, anthropic.APITimeoutError)):
            reason = cause.reason if isinstance(cause, ModelTimeoutError) else "attempt"
            logger.warning(
                "stage '%s' timed out (reason=%s)%s", error.step, reason, spent
            )
        else:
            logger.warning(
                "stage '%s' failed (%s)%s", error.step, type(cause).__name__, spent
            )
        if not isinstance(error.__cause__, UPSTREAM_CAUSES):
            raise
        status = (
            504
            if isinstance(
                error.__cause__, (ModelTimeoutError, anthropic.APITimeoutError)
            )
            else 502
        )
        raise HTTPException(status, detail=f"{_failure(error)}{spent}") from error


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


def _spent(error: StageError | ModelTimeoutError) -> str:
    """What a failed check had cost, if it made any calls."""
    if not error.calls:
        return ""
    return (
        f". Recorded cost before stopping: {usage_text(summarize_usage(error.calls))}"
    )
