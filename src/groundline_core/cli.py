"""The `groundline-core` command line.

    uv run groundline-core omissions SOURCE SUMMARY [options]
    uv run groundline-core commissions SOURCE SUMMARY [options]

Exit codes: 0 = checked and not flagged, 1 = checked and flagged,
2 = couldn't check (bad arguments or files, invalid pipeline or profile,
missing or rejected API key, a failed stage, an unwritable --json path, or
any unexpected error). Only a finished check that flags the summary exits with 1.
"""

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import platformdirs
import typer

from groundline_core.command import EXIT_ERROR, EXIT_FLAGGED, EXIT_NOT_FLAGGED
from groundline_core.core import (
    FileCache,
    ModelClient,
    ProfileError,
    SetupError,
    SpecError,
    StageError,
    load_profile,
    load_spec,
    summarize_usage,
)
from groundline_core.evaluate import (
    DEFAULT_COMMISSIONS_PRESET,
    DEFAULT_PRESET,
    EvaluationResult,
    evaluate_sync,
)
from groundline_core.evaluators.commissions import report as commissions_report
from groundline_core.evaluators.models import ALARM_STEP, AlarmOutput
from groundline_core.evaluators.omissions import report as omissions_report
from groundline_core.evaluators.report import require_steps, result_json, usage_text
from groundline_core.keys_cli import app as keys_app
from groundline_core.presets import load_preset
from groundline_core.settings import ENV_FILE

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
        help=(
            "A pipeline spec file to run instead of the "
            f"{DEFAULT_COMMISSIONS_PRESET} preset."
        ),
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
    """What differs between the checks: the preset they run, the steps their
    report reads, and how it is written."""

    preset: str
    report_name: str
    required_steps: tuple[str, ...]
    render: Callable[..., str]


OMISSIONS = Check(
    DEFAULT_PRESET,
    "omissions",
    omissions_report.REQUIRED_STEPS,
    omissions_report.render_report,
)
COMMISSIONS = Check(
    DEFAULT_COMMISSIONS_PRESET,
    "commissions",
    commissions_report.REQUIRED_STEPS,
    commissions_report.render_report,
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
    pipeline: CommissionsPipelineOpt = None,
    profile: ProfileOpt = None,
    cache: CacheOpt = False,
    json_path: JsonOpt = None,
) -> None:
    """Find the claims in a summary that the source doesn't support, and whether
    to flag it."""
    _finish(
        lambda: _check(
            COMMISSIONS, source, summary, pipeline, profile, cache, json_path
        )
    )


def _finish(run: Callable[[], int]) -> None:
    """Run a check and exit with its code. Every error becomes exit code 2:
    exit code 1 means "flagged", so nothing else may end with it, not even a
    bug. Python's own exit code for an uncaught error is 1."""
    try:
        code = run()
    except StageError as error:
        typer.echo(f"error: {error}", err=True)
        if error.calls:
            cost = usage_text(summarize_usage(error.calls))
            typer.echo(f"  Cost before stopping: {cost}", err=True)
        code = EXIT_ERROR
    except (SetupError, SpecError, ProfileError, ValueError, OSError) as error:
        typer.echo(f"error: {error}", err=True)
        code = EXIT_ERROR
    except Exception as error:
        typer.echo(
            f"error: unexpected {type(error).__name__}: {error}\n"
            "  This is a bug in groundline; please report it.",
            err=True,
        )
        code = EXIT_ERROR
    raise typer.Exit(code)


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
    require_steps(spec, check.required_steps, report=check.report_name)
    domain = load_profile(profile) if profile else None

    stage_cache = None
    if cache:
        stage_cache = FileCache(cache_dir())
        typer.echo(CACHE_WARNING.format(path=stage_cache.root), err=True)

    started = time.perf_counter()
    result = evaluate_sync(
        source.read_text(),
        summary.read_text(),
        spec=spec,
        profile=domain,
        cache=stage_cache,
        client=make_client(),
        env_file=ENV_FILE,
    )
    seconds = time.perf_counter() - started

    flagged = result.run.output(ALARM_STEP, AlarmOutput).flagged
    typer.echo(
        check.render(result, source=source.name, summary=summary.name, seconds=seconds)
    )
    if json_path:
        _write_json(json_path, result)
    return EXIT_FLAGGED if flagged else EXIT_NOT_FLAGGED


def _write_json(path: Path, result: EvaluationResult) -> None:
    try:
        path.write_text(json.dumps(result_json(result), indent=2) + "\n")
    except OSError as error:
        raise OSError(
            f"couldn't write the JSON result to {path}: {error.strerror or error}"
        ) from error
