"""What every check endpoint shares: the request, the common response parts, and
how a failed check becomes an HTTP error.

Every finished check answers 200, flagged or not (ADR 0008). No response or log
line ever holds text from the source or summary: error messages are either
written to be safe, or left to `groundline_core.api.errors`, which never repeats one.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated, Any, Literal

import anthropic
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from groundline_core.api.errors import (
    AUTHENTICATION_UNAVAILABLE,
    INVALID_API_KEY,
    UNEXPECTED,
)
from groundline_core.core import (
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
from groundline_core.evaluate import default_registry, model_users
from groundline_core.evaluators.models import AlarmOutput, Severity
from groundline_core.evaluators.report import usage_text
from groundline_core.profiles import load_builtin_profile
from groundline_core.result import EvaluationResult
from groundline_core.settings import ENV_FILE, MissingAPIKeyError

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
"""The statuses any check can answer besides 200, 502 and 503, with examples. FastAPI's
own 422 entry is replaced, since groundline's 422 leaves out the rejected value.
Check routes use `check_responses`, which adds authentication's 401 and the
502 and 503, whose examples depend on the route's pipeline."""

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
    """Every error status a check route can answer, for its `responses`:
    `PROTECTED_RESPONSES`, a 502 whose examples fail in the pipeline's first
    model call, priced at its model, and a 503 that names the stages needing
    the Anthropic API key, as the real one does."""
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
        "The model or Anthropic's API failed. Retrying later may work.",
        unusable_reply=(
            "Unusable reply",
            {
                "detail": f"{failed}: unusable reply after 1 retry: no JSON object "
                f"in the reply (the reply was 8 characters). Cost before stopping: "
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
                    model_users(pipeline), ENV_FILE
                ).api_message()
            },
        ),
    )
    return PROTECTED_RESPONSES | {502: upstream, 503: unavailable}


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


class Counts(ApiModel):
    total: int
    """Facts found in the source."""
    present: int
    """Facts the summary fully records."""
    partial: int
    """Facts the summary only partly records."""
    absent: int
    """Facts the summary leaves out."""


class Finding(ApiModel):
    """One omission or one commission, as a check reports it. The same fields serve
    both kinds, so a check that finds both can return them in one list."""

    type: Literal["omission", "commission"]
    """`omission`: a fact from the source that the summary leaves out or only
    partly records. `commission`: a claim in the summary that the source doesn't
    support."""
    severity: Severity | None
    """How much it would matter if the fact were left out, or the claim were wrong.
    Null when the check didn't grade it."""
    verdict: Literal["partial", "absent", "unsupported", "contradicted"]
    """For an omission, `partial` (the summary records only part of the fact) or
    `absent`. For a commission, `unsupported` (the source doesn't mention the
    claim) or `contradicted` (the source says otherwise)."""
    confidence: float | None
    """From 0 to 1: how sure the check is. For an omission, that the fact is
    missing; for a commission, that the source doesn't support the claim. Null
    when the check gave none."""
    statement: str
    """The fact from the source that the summary leaves out, or the words in the
    summary that make the claim, word for word."""
    source_evidence: str | None
    """The passage of the source behind the finding. For an omission, the passage
    the fact came from. For a contradicted commission, the passage the model quoted
    as contradicting the claim. Null for an unsupported commission, or if the model
    quoted none."""
    summary_evidence: str | None
    """For a partial omission, the words in the summary that partly record the
    fact. Null otherwise."""
    evidence_found: bool | None
    """Whether `source_evidence` appears in the source word for word, ignoring
    case, punctuation and spacing. True means verified. False only means it
    couldn't be matched, which a reworded quote or markup in the source, such as a
    scribe's tags, can cause; it doesn't mean the passage was invented. Null when
    there is no evidence."""
    explanation: str
    """What kind of problem this is, in a fixed sentence for each type and verdict.
    It is the same for every finding of that kind, so use the other fields for the
    details."""


class Metadata[A: Alarm](ApiModel):
    """How the check ran and how it decided: what ran, the alarm behind the flag,
    and what it cost."""

    pipeline: str
    """The pipeline that ran. It is a validated preset, whose measured results
    apply to this run."""
    alarm: A
    """The alarm score, the threshold it was compared with, and the threshold
    level. The summary is flagged when the alarm score reaches the threshold."""
    usage: Usage
    """The model calls the check made, and what they cost."""


class CheckResponse[E: str, M: ApiModel](ApiModel):
    """What every check answers: the same top-level fields, whichever check ran."""

    evaluation_type: E
    """Which check made this response: `omissions` or `commissions`."""
    flagged: bool
    """Whether the alarm score reached the threshold."""
    score: float
    """From 0 to 1. Higher is better. A score below 0.5 means the summary is
    flagged, and a summary passes at 0.5 or above; the flag is exactly the
    alarm's."""
    findings: list[Finding]
    """What the check found, most important first."""
    counts: Counts | None
    """How many of the source's facts the summary records, fully, partly or not at
    all. Null for a check that doesn't count facts, such as the commission check."""
    metadata: M
    """How the check ran: the pipeline, the alarm behind the flag, and the usage."""


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
        raise HTTPException(503, detail=error.api_message()) from error
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
