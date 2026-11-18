"""The `groundline-core` command line.

    uv run groundline-core omissions SOURCE SUMMARY [options]
    uv run groundline-core commissions SOURCE SUMMARY [--preset NAME] [options]

Exit codes: 0 = checked and not flagged, 1 = checked and flagged,
2 = couldn't check (bad arguments or files, invalid pipeline or profile,
missing or rejected API key, a failed stage, an unwritable --json path, or
any unexpected error). Only a finished check that flags the summary exits with 1.
"""

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, get_args

import platformdirs
import typer

from groundline_core._core import (
    FileCache,
    GroundlineError,
    InvalidInputError,
    ModelClient,
    ModelFailureError,
    file_line,
    load_profile,
    load_spec,
    redact_keys,
    summarize_usage,
)
from groundline_core._core.file_problems import os_reason, read_text
from groundline_core._evaluate import (
    DEFAULT_COMMISSIONS_PRESET,
    DEFAULT_PRESET,
    CommissionPreset,
    EvaluationResult,
    evaluate_sync,
)
from groundline_core._evaluators.commissions import report as commissions_report
from groundline_core._evaluators.omissions import report as omissions_report
from groundline_core._evaluators.report import result_json, usage_text
from groundline_core._findings import CHECK_NAMES, evaluation_type_of
from groundline_core._result import EvaluationType
from groundline_core._settings import ENV_FILE
from groundline_core.command import EXIT_ERROR, EXIT_FLAGGED, EXIT_NOT_FLAGGED
from groundline_core.keys_cli import app as keys_app
from groundline_core.presets import load_preset

app = typer.Typer(
    help="Check AI-written summaries against their source.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
app.add_typer(keys_app, name="keys")

CACHE_WARNING = (
    "warning: caching is on. The cache stores sources, summaries and the facts "
    "taken from them in {path}, so don't use it with sensitive data."
)


def make_client() -> ModelClient | None:
    """The model client to use; None means the default Anthropic client.
    Tests replace this with a scripted fake."""
    return None


def cache_dir() -> Path:
    """Outside the repo: the platform cache folder, or GROUNDLINE_CACHE_DIR."""
    override = os.environ.get("GROUNDLINE_CACHE_DIR")
    return (
        Path(override) if override else Path(platformdirs.user_cache_dir("groundline"))
    )


@app.callback()
def main() -> None:
    """Check AI-written summaries against their source."""


# The arguments and options every check command takes.

SourceArg = Annotated[
    Path,
    typer.Argument(
        exists=True, dir_okay=False, help="The source, e.g. a consultation transcript."
    ),
]
SummaryArg = Annotated[
    Path,
    typer.Argument(
        exists=True, dir_okay=False, help="The summary to check, e.g. a clinical note."
    ),
]
OmissionsPipelineOpt = Annotated[
    Path | None,
    typer.Option(
        exists=True,
        dir_okay=False,
        help=f"A pipeline spec file to run instead of the {DEFAULT_PRESET} preset.",
    ),
]
CommissionsPipelineOpt = Annotated[
    Path | None,
    typer.Option(
        exists=True,
        dir_okay=False,
        help="A pipeline spec file to run instead of a preset.",
    ),
]
COMMISSION_PRESETS: tuple[str, ...] = get_args(CommissionPreset)
CommissionsPresetOpt = Annotated[
    str | None,
    typer.Option(
        metavar="<name>",
        help=(
            "Which validated preset to run: "
            + " or ".join(
                f"{name} (the default)" if name == DEFAULT_COMMISSIONS_PRESET else name
                for name in COMMISSION_PRESETS
            )
            + ". The lean preset isn't confirmed to its pre-registered standard "
            "(see the README)."
        ),
        show_default=False,
    ),
]
ProfileOpt = Annotated[
    Path | None,
    typer.Option(
        exists=True,
        file_okay=False,
        help="A domain profile folder, instead of the pipeline's built-in one.",
    ),
]
CacheOpt = Annotated[
    bool,
    typer.Option("--cache", help="Cache each stage's output (not for sensitive data)."),
]
JsonOpt = Annotated[
    Path | None,
    typer.Option(
        "--json", help="Also write the full result as JSON (includes source text)."
    ),
]


@dataclass(frozen=True)
class Check:
    """What differs between the checks: the preset they run, which check a
    pipeline must carry out, and how the report is written."""

    preset: str
    evaluation_type: EvaluationType
    render: Callable[..., str]


OMISSIONS = Check(DEFAULT_PRESET, "omissions", omissions_report.render_report)
COMMISSIONS = Check(
    DEFAULT_COMMISSIONS_PRESET, "commissions", commissions_report.render_report
)


@app.command()
def omissions(
    source: SourceArg,
    summary: SummaryArg,
    pipeline: OmissionsPipelineOpt = None,
    profile: ProfileOpt = None,
    cache: CacheOpt = False,
    json_path: JsonOpt = None,
) -> None:
    """Find the facts a summary leaves out, and whether to flag it."""
    _finish(
        lambda: _check(OMISSIONS, source, summary, pipeline, profile, cache, json_path)
    )


@app.command()
def commissions(
    source: SourceArg,
    summary: SummaryArg,
    preset: CommissionsPresetOpt = None,
    pipeline: CommissionsPipelineOpt = None,
    profile: ProfileOpt = None,
    cache: CacheOpt = False,
    json_path: JsonOpt = None,
) -> None:
    """Find the claims in a summary that the source doesn't support, and whether
    to flag it."""
    _finish(
        lambda: _check(
            _commissions_check(preset, pipeline),
            source,
            summary,
            pipeline,
            profile,
            cache,
            json_path,
        )
    )


class UsageError(Exception):
    """A mistake in how the command was run, such as two options that can't go
    together. The message says how to run it instead. Only the CLI raises it,
    so it isn't a `GroundlineError`."""


class JSONWriteError(Exception):
    """The `--json` file couldn't be written. The check itself finished, and its
    report has already been printed. The message names the file; `path` is its
    full path, which the CLI prints on a `File:` line of its own."""

    def __init__(self, message: str, *, path: Path) -> None:
        super().__init__(message)
        self.path = path.absolute()


def _commissions_check(preset: str | None, pipeline: Path | None) -> Check:
    """The commission check with the preset the caller chose, if any. Raises
    before anything runs if it is unknown or comes with a pipeline spec file."""
    if preset is None:
        return COMMISSIONS
    if pipeline:
        raise UsageError(
            "use --preset or --pipeline, not both: "
            "a pipeline spec file replaces the preset."
        )
    if preset not in COMMISSION_PRESETS:
        raise UsageError(
            f"unknown preset '{preset}'. Use {' or '.join(COMMISSION_PRESETS)}."
        )
    return replace(COMMISSIONS, preset=preset)


class _WarningLines(logging.Handler):
    """Prints groundline's own warning log records, such as a cache that can't
    be written, the way the CLI prints its own warnings."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        typer.echo(f"warning: {record.getMessage()}", err=True)


def _finish(run: Callable[[], int]) -> None:
    """Run a check and exit with its code. Every error becomes exit code 2:
    exit code 1 means "flagged", so nothing else may end with it, not even a
    bug. Python's own exit code for an uncaught error is 1."""
    library_logs = logging.getLogger("groundline")
    handler = _WarningLines()
    library_logs.addHandler(handler)
    try:
        code = _exit_code(run)
    finally:
        library_logs.removeHandler(handler)
    raise typer.Exit(code)


def _exit_code(run: Callable[[], int]) -> int:
    """The check's exit code, or EXIT_ERROR after printing what stopped it."""
    try:
        return run()
    except (GroundlineError, UsageError, JSONWriteError) as error:
        # Every message is redacted here, including the CLI's own errors, which
        # can quote a name from a spec file; file_line redacts its own line.
        # SetupError and JSONWriteError carry the full path of the file at
        # fault; the others have none. A --json file was never written, so it
        # isn't marked "(not found)".
        path: Path | None = getattr(error, "path", None)
        file = file_line(path, note_missing=not isinstance(error, JSONWriteError))
        typer.echo(f"error: {redact_keys(str(error))}{file}", err=True)
        if isinstance(error, ModelFailureError) and error.calls:
            cost = usage_text(summarize_usage(error.calls))
            typer.echo(f"  Recorded cost before stopping: {cost}", err=True)
    except Exception as error:
        # Only the type: a bug's message is text groundline didn't write, such as
        # a pydantic error quoting the model's reply, which can repeat the source.
        typer.echo(
            f"error: unexpected {type(error).__name__}\n"
            "  This is a bug in groundline; please report it.",
            err=True,
        )
    return EXIT_ERROR


def _check(
    check: Check,
    source: Path,
    summary: Path,
    pipeline: Path | None,
    profile: Path | None,
    cache: bool,
    json_path: Path | None,
) -> int:
    """Run the check, print the report, and return the exit code."""
    spec = load_spec(pipeline) if pipeline else load_preset(check.preset)
    evaluation_type = evaluation_type_of(spec)
    if evaluation_type != check.evaluation_type:
        raise UsageError(
            f"pipeline '{spec.name}' carries out the {CHECK_NAMES[evaluation_type]}: "
            f"run it with groundline-core {evaluation_type}"
        )
    domain = load_profile(profile) if profile else None

    stage_cache = None
    if cache:
        stage_cache = FileCache(cache_dir())
        typer.echo(CACHE_WARNING.format(path=stage_cache.root), err=True)

    started = time.perf_counter()
    result = evaluate_sync(
        _input_text(source),
        _input_text(summary),
        spec=spec,
        profile=domain,
        cache=stage_cache,
        client=make_client(),
        env_file=ENV_FILE,
    )
    seconds = time.perf_counter() - started

    typer.echo(
        check.render(result, source=source.name, summary=summary.name, seconds=seconds)
    )
    if json_path:
        _write_json(json_path, result)
    return EXIT_FLAGGED if result.flagged else EXIT_NOT_FLAGGED


def _input_text(path: Path) -> str:
    """A source or summary file's text. One that can't be read is invalid input,
    named by its file name only, as every groundline error message is."""
    return read_text(path, InvalidInputError)


def _write_json(path: Path, result: EvaluationResult) -> None:
    try:
        path.write_text(json.dumps(result_json(result), indent=2) + "\n")
    except OSError as error:
        raise JSONWriteError(
            f"couldn't write the JSON result to {path.name}: {os_reason(error)}",
            path=path,
        ) from None
