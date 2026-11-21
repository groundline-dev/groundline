# Using groundline from Python

The `groundline-core` package runs groundline's checks inside your own Python code: the omission check, which reports the important facts a summary leaves out, and the commission check, which reports the claims in a summary that its source doesn't support. The [README](../README.md) explains how each check works and how well it did on its benchmark. Read its [Disclaimer](../README.md#disclaimer) before relying on a result.

## Library, CLI or API

groundline can be used three ways, and they differ in who pays for the model calls and where your text goes:

| | Who pays for the model calls | Where your text goes |
|---|---|---|
| Library (`groundline_core` in your Python code) | You, with your own Anthropic API key | From your process to Anthropic |
| CLI (`groundline-core omissions` or `commissions`) | You, with your own Anthropic API key | From your machine to Anthropic, and into a cache on your disk if you pass `--cache` |
| API (over HTTP, or from Python with the [SDK](../packages/groundline-client/README.md)) | Whoever runs the deployment, with its Anthropic API key | From your app to the deployment, and from there to Anthropic. The deployment's database holds API keys, not your text ([ADR 0009](adr/0009-the-database-holds-no-patient-data.md)) |

Use the library when a Python app should run checks with its own Anthropic API key, the CLI to check files from a terminal or a script, and the API when several apps, or apps in other languages, should share one deployment and its key.

## Install

The package needs Python 3.13 or later. A plain install brings only what the checks need: `anthropic`, `pydantic-settings` and `pyyaml`. Add an extra for the rest:

<!-- not run: installs the package -->
```sh
pip install groundline-core              # the library only
pip install "groundline-core[cli]"       # plus the groundline-core command and key management
pip install "groundline-core[server]"    # plus the API
```

In a uv project, add it with `uv add`:

<!-- not run: installs the package -->
```sh
uv add groundline-core
```

The package is installed as `groundline-core` and imported as `groundline_core`, because an unrelated project already uses the name `groundline` on PyPI ([ADR 0016](adr/0016-published-as-groundline-core.md)). Without the `cli` extra, the `groundline-core` command says which extra to install, and so does importing `groundline_core.app` without the `server` extra.

## Run a check

The examples below read a consultation from `transcript.txt` and the note written from it from `note.txt`, so put your own text in those two files. They take the Anthropic API key from `ANTHROPIC_API_KEY` in the environment (see [The Anthropic API key](#the-anthropic-api-key) for the other ways).

**Each run costs money.** The omission check costs about $0.21 for a typical clinical note and takes about a minute, and the quick commission check costs about $0.001 and takes a few seconds.

### The omission check

`evaluate_sync` runs the omission check unless you pass another spec:

```python
from pathlib import Path

from groundline_core import evaluate_sync

transcript_text = Path("transcript.txt").read_text()
note_text = Path("note.txt").read_text()

result = evaluate_sync(transcript_text, note_text)

print(result.flagged, result.score)  # for example: True 0.15
for finding in result.findings:
    print(finding.severity, finding.verdict, finding.confidence, finding.statement)
print(result.counts)  # for example: total=35 present=29 partial=3 absent=3
print(result.alarm)  # for example: score=0.97 threshold=0.9 level='strict'
print(result.validated_preset)  # "omission_all_opus", or None if the check was changed
print(result.usage.cost_usd)
```

### The commission check

For the commission check, pass its preset as the `spec`. The result has the same fields, and `counts` is `None`:

```python
from pathlib import Path

from groundline_core import DEFAULT_COMMISSIONS_PRESET, evaluate_sync, load_preset

transcript_text = Path("transcript.txt").read_text()
note_text = Path("note.txt").read_text()

result = evaluate_sync(
    transcript_text, note_text, spec=load_preset(DEFAULT_COMMISSIONS_PRESET)
)

print(result.flagged, result.score)
for finding in result.findings:
    print(finding.severity, finding.verdict, finding.confidence, finding.statement)
```

`DEFAULT_COMMISSIONS_PRESET` is the quick check. To run the lean one, pass `load_preset("commission_reverse_lean")`.

### From async code

`evaluate()` is the async version, for FastAPI or other async code. It takes the same arguments as `evaluate_sync`:

```python
import asyncio
from pathlib import Path

from groundline_core import evaluate


async def main() -> None:
    transcript_text = Path("transcript.txt").read_text()
    note_text = Path("note.txt").read_text()

    result = await evaluate(transcript_text, note_text)
    print(result.flagged, result.score)


asyncio.run(main())
```

The commission check works the same way:

```python
import asyncio
from pathlib import Path

from groundline_core import DEFAULT_COMMISSIONS_PRESET, evaluate, load_preset


async def main() -> None:
    transcript_text = Path("transcript.txt").read_text()
    note_text = Path("note.txt").read_text()

    result = await evaluate(
        transcript_text, note_text, spec=load_preset(DEFAULT_COMMISSIONS_PRESET)
    )
    print(result.flagged, result.score)


asyncio.run(main())
```

### What the result holds

The fields mean what the API's do (see [Findings](../README.md#findings) and [Scores](../README.md#scores)), and `None` stands for the API's `null`. What the API puts in `metadata` is in separate fields in Python: `alarm`, `usage`, `pipeline` (the name of the pipeline that ran), `validated_preset` (the preset it matched) and `disclaimer` (the [Disclaimer](../README.md#disclaimer), to show with the result). `result.run` holds every step's output for inspection; its shape follows the pipeline's steps and may change without notice.

A check can take up to two minutes, its time limit, because it makes several model calls. A spec has to make one full check: the alarm step, plus the steps one check's findings come from. Anything else raises `SpecError` before the key is looked up or a model is called. `evaluate` and `evaluate_sync` also accept `profile`, `cache` and `registry`, but those take internal types, so they aren't part of the public interface yet.

## The Anthropic API key

The library takes the Anthropic API key from the `anthropic_api_key` argument, or else from `ANTHROPIC_API_KEY` in the environment. Pass it as an argument when your app keeps it somewhere else, such as a secret store:

```python
import os
from pathlib import Path

from groundline_core import evaluate_sync

my_key = os.environ["ANTHROPIC_API_KEY"]  # or read it from your own secret store
transcript_text = Path("transcript.txt").read_text()
note_text = Path("note.txt").read_text()

result = evaluate_sync(transcript_text, note_text, anthropic_api_key=my_key)
```

The library reads a `.env` file only when you name one with `env_file`, so it doesn't matter which folder your app runs from:

```python
from pathlib import Path

from groundline_core import evaluate_sync

transcript_text = Path("transcript.txt").read_text()
note_text = Path("note.txt").read_text()

result = evaluate_sync(transcript_text, note_text, env_file="config/groundline.env")
```

The argument wins over the environment, and the environment wins over `env_file`. A blank argument raises an error; it doesn't fall back to the environment or `env_file`. With no key, `evaluate_sync` raises `MissingAPIKeyError` before any paid call. Its message names an `env_file` only by its name; the error's `path` holds the full path. The key never appears in logs, errors or the result. Keep `.env` files out of version control.

## A model client

To make the model calls yourself, pass a model client as `client`. It is any object with one async method, `complete(prompt, *, model, effort, max_tokens)`, that returns a `ModelReply`. This one makes the calls through its own Anthropic SDK client, which is where you would point it at a gateway or add your own logging:

```python
from pathlib import Path

from anthropic import AsyncAnthropic
from groundline_core import ModelReply, evaluate_sync


class LoggingModelClient:
    def __init__(self) -> None:
        self._anthropic = AsyncAnthropic()  # reads ANTHROPIC_API_KEY

    async def complete(
        self, prompt: str, *, model: str, effort: str, max_tokens: int
    ) -> ModelReply:
        message = await self._anthropic.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort},
        )
        print(f"{model}: {message.usage.input_tokens} tokens in")
        text = "".join(block.text for block in message.content if block.type == "text")
        return ModelReply(
            text=text,
            model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )


transcript_text = Path("transcript.txt").read_text()
note_text = Path("note.txt").read_text()

result = evaluate_sync(transcript_text, note_text, client=LoggingModelClient())
```

A fake model client works the same way, so your app's tests can run checks without paying for them. Pass `client` or `anthropic_api_key`, not both: a model client brings its own key. The check's time limit still applies to a model client's calls. An exception your model client raises comes out of the check as it was raised; to report a failure as a `ModelFailureError`, raise one yourself (see [Errors](#errors)).

## Caching

**The cache stores sources and summaries on disk, so don't cache sensitive data.** It's off by default: the library caches only when it's given a cache, which isn't part of the public interface yet, and the CLI caches only with `--cache`. The README's [Caching and sensitive data](https://github.com/groundline-dev/groundline#caching-and-sensitive-data) says where the cache is kept and what else it holds.

## Errors

Every error groundline raises on purpose is a `GroundlineError`, of one of three kinds:

| Error | Raised when | What to do |
|---|---|---|
| `SetupError` | The setup stops the check: an invalid spec (`SpecError`) or a domain profile it can't load (`ProfileError`), a missing key (`MissingAPIKeyError`), a rejected key (`APIKeyRejectedError`), a model the Anthropic API doesn't have, settings that can't be read (`SettingsError`), or both `client` and `anthropic_api_key` passed. | Fix the setup. Trying again won't help. |
| `InvalidInputError` | The source or summary isn't text, or is empty or only whitespace. It's raised before the key is looked up or a model is called. | Fix the input. |
| `ModelFailureError` | Anthropic's API kept failing, the model declined, or its reply stayed unusable. `ModelTimeoutError`, a kind of `ModelFailureError`, means a time limit ran out. `step` names the step that failed, or is `None` when the whole check ran out of time between steps. | Try again later. |

```python
from pathlib import Path

from groundline_core import InvalidInputError, ModelFailureError, SetupError, evaluate_sync

transcript_text = Path("transcript.txt").read_text()
note_text = Path("note.txt").read_text()

try:
    result = evaluate_sync(transcript_text, note_text)
except ModelFailureError as error:
    print("try again later:", error.step)  # the step that failed, such as "extract"
except SetupError as error:
    print("fix the setup:", error, error.path)  # path is the file at fault, or None
except InvalidInputError as error:
    print("the source or summary can't be checked:", error)
```

A message is safe to log or show: it never quotes the source, the summary, the key or Anthropic's own error text, and it names a file only by its name. It does quote names from a spec or profile, such as an unknown step, with anything shaped like an Anthropic or groundline API key replaced by `<redacted key>`; other kinds of secret aren't recognized. When groundline's error comes from another library's error, such as YAML's, pydantic's or the Anthropic SDK's, a traceback doesn't print that original, since its text could quote a file, a setting or the request. `SetupError.path` holds the file's full path, and the CLI prints it on a `File:` line of its own. `SpecError`, `ProfileError` and `InvalidInputError` are also `ValueError`s, but `ModelTimeoutError` isn't a `TimeoutError`.

An exception from your own model client, or from a bug in groundline, comes out as it was raised rather than as a `GroundlineError` ([ADR 0020](adr/0020-only-groundlines-own-failures-are-groundline-errors.md)), and it doesn't say what the check had spent.

## Public interface

Import from `groundline_core` itself. Its names, and the documented fields of the result types, keep working across upgrades. Everything else, including the modules whose names start with an underscore (such as `groundline_core._core`) and `result.run`, is internal and may change without notice.

| Name | What it is |
|---|---|
| `evaluate`, `evaluate_sync` | Run a check. |
| `load_preset`, `builtin_presets` | Load a built-in preset by name, and list the names. |
| `DEFAULT_PRESET`, `DEFAULT_COMMISSIONS_PRESET`, `CommissionPreset` | The default omission and commission presets, and the commission presets' names as a type. |
| `PipelineSpec` | A pipeline spec, as `load_preset` returns it. |
| `ModelClient`, `ModelReply` | What a model client implements (an async `complete(prompt, *, model, effort, max_tokens)`), and what it returns. |
| `EvaluationResult`, `Finding`, `Counts`, `Alarm`, `Usage` | The result and its parts. |
| `GroundlineError`, `SetupError`, `SpecError`, `ProfileError`, `MissingAPIKeyError`, `APIKeyRejectedError`, `SettingsError`, `InvalidInputError`, `ModelFailureError`, `ModelTimeoutError` | What a check raises (see [Errors](#errors)). |
| `EvaluationType`, `Severity` | The types of `evaluation_type` (`omissions` or `commissions`) and of a finding's `severity` (`critical`, `supporting` or `peripheral`). |

The package includes its type information, so type checkers such as pyright and mypy check your code against these names.
