# groundline

groundline checks an AI-written summary against its source. Its **omission check** reports which important facts the summary leaves out, how much each one matters, and whether the summary should be flagged. Its **commission check** (often called hallucination detection) reports which claims in the summary the source doesn't support, either because it contradicts them or doesn't mention them. It comes with one domain profile, for clinical notes and the consultation transcripts they're written from; other domains need their own profile.

**Status:** early development. It is research code, **not for clinical use**, and must not be used with real patient data yet (see [Caching and sensitive data](#caching-and-sensitive-data)). Read the [Disclaimer](#disclaimer) before relying on a result.

## Ways to use groundline

- **Python library:** import `groundline_core` to run checks inside a Python app (see [Using it from Python](#using-it-from-python)).
- **Command line:** `groundline-core omissions` or `groundline-core commissions` checks files from a terminal or script (see [Checking for omissions](#checking-for-omissions) and [Checking for commissions](#checking-for-commissions)).
- **HTTP API:** run the server when several apps, or apps in other languages, need to call groundline over HTTP (see [Using the API](#using-the-api)).

## How it works

The default pipeline, the **all-Opus omission check**, runs six steps:

1. **Extract:** list the facts a complete summary should record, each with a quote from the source.
2. **Rank:** grade each fact critical, supporting or peripheral, using the domain's severity rubric.
3. **Check:** one call decides, for every fact, whether the summary records it (present, partial or absent), with a 0–100 confidence that it's missing.
4. **Refute:** a second look at flagged facts. It can only clear a flag, and only with a verified quote from the summary.
5. **Score:** counts and ratios of what's missing.
6. **Alarm:** flag the summary when a critical fact is confidently missing. The alarm score is the highest missing confidence among critical facts still flagged, with partials at half weight.

Every step uses Claude Opus 5.5. On 50 held-out notes from [OmissionBench](https://huggingface.co/datasets/ComposoAI/OmissionBench), with thresholds fixed in advance, it caught 16/50 omissions with 1/50 clean notes flagged, at about $0.21 per note. Those results hold only for the unchanged `omission_all_opus` preset. The report tells you whether a run used it.

The default commission pipeline, the **quick commission check**, runs three steps:

1. **Commissions check:** one Claude Haiku 5.5 call lists the summary's claims that the source doesn't support. Each has the summary's own words, whether the source contradicts it or just doesn't say it, a 0–100 confidence that it's unsupported, and how much it would matter if it were wrong. A contradicted claim also gets the passage of the source that contradicts it.
2. **Commissions refute:** a second look at the critical flags. It can only clear a flag, and only with a verified quote from the source. It is switched off in the preset.
3. **Alarm:** flag the summary when a critical claim is confidently unsupported. The alarm score is the highest unsupported confidence among critical claims still flagged, and the one threshold level, `default`, is 0.85.

Measured on 172 held-out commission pairs (pre-registered), it caught 151, flagged 1 of 110 clean notes, and cost about $0.001 per note. It passed its pre-registered test. Those results hold only for the unchanged `commission_quick` preset.

On realistic notes outside that benchmark, it flagged about a third of them. A blind review found that most of those flags were on genuine content the transcript doesn't say aloud, such as exam findings, which the check can't tell apart from invented content. A flag means the note needs a look; it isn't proof that anything was invented.

The quick check uses Claude Haiku 5.5, not Opus (see [ADR 0007](https://github.com/groundline-dev/groundline/blob/main/docs/adr/0007-commission-check-ships-quick.md)).

groundline can also run the **lean commission check** (`commission_reverse_lean`), which uses Claude Opus 5.5 throughout:

1. **Commissions claims:** one call lists every claim the summary makes, each as a short statement with the summary's own words.
2. **Commissions check:** one call judges each claim against the source as supported, unsupported or contradicted, with a confidence and a severity. Only a contradicted claim needs a quote from the source, and that quote must be found word for word.
3. **Commissions refute:** a second look at every flagged claim, whatever its severity, which can only clear one, and only with a verified quote from the source.
4. **Alarm:** as above, with its own threshold level, `default`, at 0.80.

Measured on 172 commission pairs, it caught 166, flagged 0 of 110 clean notes, and cost about $0.14 and 19 seconds per note. It matched the exact preset's catches, but failed both of its pre-registered tests, a paired-score margin less than two pairs wide and a false-alarm limit on notes from other sources, so it isn't confirmed to that standard (see [ADR 0017](https://github.com/groundline-dev/groundline/blob/main/docs/adr/0017-the-commission-check-also-offers-lean.md)). Those results hold only for the unchanged `commission_reverse_lean` preset. The CLI's `commissions` command runs quick unless you pass `--preset commission_reverse_lean`.

## Setup

This section sets up a clone of the repository for development. To use groundline in your own code, install the package instead (see [Using it from Python](#using-it-from-python)).

This project uses [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13 (0.13.7 or later); check with `uv --version`. After cloning, create the virtualenv, install dependencies and enable the shared git hooks:

```sh
make setup
```

`make` targets sync automatically; run `uv sync` after a pull if your editor shows missing imports. Use `uv run <command>` to run anything else inside the environment without activating it. The examples in this README run from a clone like this; if you installed the package with pip instead, leave out `uv run`, as in `groundline-core omissions transcript.txt note.txt`.

Model calls need an Anthropic API key. Copy `.env.example` to `.env` and add your key, or export `ANTHROPIC_API_KEY`. groundline stores its own API-key hashes and metadata in PostgreSQL. HTTP checks require PostgreSQL for authentication; CLI and Python checks do not. [DEVELOPMENT.md](https://github.com/groundline-dev/groundline/blob/main/DEVELOPMENT.md#getting-started) explains how to set it up. `.env` is git-ignored; never commit a key. The CLI and the API read `.env` from the folder you run them in, so run commands from the repo root. The Python library reads a `.env` file only if you name one (see [The Anthropic API key in your code](#the-anthropic-api-key-in-your-code)). A missing or rejected Anthropic API key produces an error saying where groundline looked and how to fix it, before any paid call.

**Live calls cost money.** A typical clinical note costs about $0.21 for the omission check, and about $0.001 for the quick commission check or $0.14 for the lean one. Agree a budget with your team before running anything at scale.

## Managing API keys

These are groundline API keys for callers of protected HTTP endpoints, separate from the Anthropic API key used for model calls. Complete the [database setup](https://github.com/groundline-dev/groundline/blob/main/DEVELOPMENT.md#getting-started) first; these commands access PostgreSQL directly and do not require the API server to be running or an Anthropic API key.

```sh
uv run groundline-core keys create
uv run groundline-core keys create --name "Avi's laptop" --expires-in 90d
uv run groundline-core keys list
uv run groundline-core keys revoke <key-id>
```

Creation prints the name, full UUID identifier, expiry, and plaintext API key **once, after the database commit succeeds**. Copy the secret then: only its SHA-256 hash, a short display prefix, and metadata are stored. If terminal output fails after creation, the API key may already exist; use the identifier in the error to revoke it and create a replacement.

- Without `--name`, a friendly label such as `nimble-otter-a7c91e2b` is generated. Custom names must be non-empty, printable on one line, and at most 128 characters after trimming surrounding spaces. Existing names are unchanged. Names may repeat; revocation uses the full identifier, not the name or secret.
- Without `--expires-in`, the API key never expires. Lifetimes are positive whole numbers followed by `s`, `m`, `h`, or `d`, such as `30m`, `12h`, or `90d`. Expiry is measured from creation and displayed in UTC.
- Revocation is permanent. Repeating it succeeds without changing the original revocation timestamp. An unknown identifier is an error. Commands exit 0 on success and 2 on error; errors go to stderr.

`keys list` shows every API key newest first in aligned columns, with its full UUID, name, identifying prefix, status, and UTC expiry (`never` when unset). Values are not truncated. It includes revoked and expired keys; an active key past its expiry displays as `expired` without changing its stored status. No credentials or hashes are shown. An empty database prints `No API keys found.` Prefixes help identify a key but are not guaranteed unique: use the full UUID to revoke it.

Callers send `Authorization: Bearer <api-key>` to a protected route. Authentication checks the primary database on every request, so revocation affects subsequent checks across workers and app instances; requests already authorized may finish. Invalid, revoked, or expired API keys receive 401; unavailable authentication storage produces 503. `/health` and `/ready` remain public. The reusable dependency is available as described in [DEVELOPMENT.md](https://github.com/groundline-dev/groundline/blob/main/DEVELOPMENT.md#protecting-an-endpoint). `POST /v1/omissions` and `POST /v1/commissions` use this dependency. Missing API keys also receive 401; all credential rejections use `{"detail": "Invalid or missing API key"}` and `WWW-Authenticate: Bearer`.

A connection error or timeout during commit does not prove the change failed. The database may have saved it before the connection dropped. Groundline does not automatically repeat the operation.

- **If you were creating a key:** run `uv run groundline-core keys list`. Newest entries appear first. Look for an entry matching the name you supplied, if any, but remember that names can repeat. If you can identify the new key and never received its secret, revoke it using `uv run groundline-core keys revoke <key-id>`, then create a replacement. The secret cannot be retrieved later. If you cannot tell which entry is yours, investigate with the database operator before revoking anything or repeating creation.
- **If you were revoking a key:** run `uv run groundline-core keys list` and find that key by its full ID. If it is revoked, the operation succeeded. If it is still active, you can repeat the revocation. If you cannot confirm its status, investigate before repeating the operation.

## Checking for omissions

```sh
uv run groundline-core omissions transcript.txt note.txt
```

Example report:

```
Omission check   transcript.txt · note.txt
Pipeline         omission_all_opus · clinical profile (validated preset)
Result           FLAGGED (alarm 0.97 ≥ 0.90, strict)
Omission score   0.15 (higher is better; below 0.5 is flagged)

Not fully recorded (rank order):
  severity    verdict  p_missing  fact
  critical    partial  0.60       Presumptive working diagnosis is TIA (mini-stroke), to be excluded despite young age given family history of stroke.
  critical    absent   0.97       Allergic to penicillin. No other drug allergies.
  critical    partial  0.45       Advised to contact the GP back if there is any delay, cannot make the appointment, or does not get called.
  critical    absent   0.85       Advised to contact the GP service if symptoms recur, for phone assessment and guidance.
  supporting  absent   0.80       Symptoms began with episodes of marked tiredness lasting a couple of days, then returning to normal.
  supporting  partial  0.70       Intermittent nausea and lack of motivation.

35 facts: 29 present · 3 partial · 3 absent
Cost             $0.29 · 4 calls · 31,620 input / 8,140 output tokens · 48.3 s

groundline's results help a person review a summary. They aren't medical advice
or a clinical decision. A summary that isn't flagged can still leave out or
misstate important facts, so check it before relying on it. You're responsible
for any decision made using groundline's results. groundline is provided "as
is", and its authors accept no liability for outcomes from its use (see sections
7 and 8 of the Apache License 2.0).
```

### Exit codes

- `0`: checked, not flagged.
- `1`: checked and flagged.
- `2`: couldn't check: bad arguments or files, an invalid pipeline or profile, a missing or rejected Anthropic API key, a failed stage, an unwritable `--json` path, or an unexpected error. The command prints an `error:` message, and if a stage failed after making model calls, what those calls cost. When a file is at fault, such as `.env`, a `File:` line gives its full path, marked `(not found)` if it doesn't exist. An unexpected error is a bug in groundline: the command prints only its type, since its message wasn't written by groundline and could quote the source or summary, and asks you to report it. If the command can't load at all, it also exits 2: without the `cli` extra it prints which extra to install, and otherwise the error's traceback.

### Options

| Option | What it does |
|---|---|
| `--pipeline SPEC.yaml` | Run a custom pipeline spec instead of the preset. |
| `--profile DIR` | Use a domain profile folder instead of the pipeline's built-in profile. |
| `--cache` | Cache each step's output so identical re-runs are free. Off by default; see below. |
| `--json PATH` | Also write the full result as JSON: every step's output, call records, usage, the flag decision and the [disclaimer](#disclaimer). |

## Checking for commissions

```sh
uv run groundline-core commissions transcript.txt note.txt
```

Example report:

```
Commission check  transcript.txt · note.txt
Pipeline          commission_quick · clinical profile (validated preset)
Result            FLAGGED (alarm 0.95 ≥ 0.85, default)
Commission score  0.1667 (higher is better; below 0.5 is flagged)

Not supported by the source (severity order):
  severity    verdict       confidence  claim
  critical    contradicted  0.95        Motrin 800 mg every 6 hours for a week
                                        source: "take motrin 600 mg every 6 hours for a week"
  supporting  unsupported   0.70        neurovascular examination intact

Cost              $0.0020 · 1 calls · 14,820 input / 1,040 output tokens · 4.1 s

groundline's results help a person review a summary. They aren't medical advice
or a clinical decision. A summary that isn't flagged can still leave out or
misstate important facts, so check it before relying on it. You're responsible
for any decision made using groundline's results. groundline is provided "as
is", and its authors accept no liability for outcomes from its use (see sections
7 and 8 of the Apache License 2.0).
```

The [exit codes](#exit-codes) and [options](#options) are the same as for omissions, plus `--preset NAME`, which picks the validated preset to run: `commission_quick` (the default) or `commission_reverse_lean` (not confirmed to its pre-registered standard, as above). Use `--preset` or `--pipeline`, not both, since a pipeline spec file replaces the preset. An unknown name, or both options together, stops the command with exit code 2. If the model's quote of the source passage isn't found word for word in the source, the report still shows it, marked `(not found word for word in the source)`. If a pipeline switches refute on, the report adds a `Cleared by refute` line.

## Using it from Python

To use groundline inside your own app, install the `groundline-core` package from PyPI. A plain install brings only what the checks need: `anthropic`, `pydantic-settings` and `pyyaml`. Add an extra for the rest:

```sh
pip install groundline-core              # the library only
pip install "groundline-core[cli]"       # plus the groundline-core command and key management
pip install "groundline-core[server]"    # plus the API
```

`uv pip install` takes the same arguments, and `uv add` adds it to a uv project. The package is installed as `groundline-core`, imported as `groundline_core` and run as `groundline-core`, because an unrelated project already uses the name `groundline` on PyPI ([ADR 0016](https://github.com/groundline-dev/groundline/blob/main/docs/adr/0016-published-as-groundline-core.md)). Without the `cli` extra, the `groundline-core` command says which extra to install, and so does importing `groundline_core.app` without the `server` extra.

### Versions

`groundline-core` follows semantic versioning. Until 1.0, a minor release (0.1 to 0.2) may break your code and a patch release (0.1.0 to 0.1.1) never does, so pin a minor version, such as `groundline-core>=0.1,<0.2`.

### The Anthropic API key in your code

The library takes the Anthropic API key from the `anthropic_api_key` argument, or else from `ANTHROPIC_API_KEY` in the environment. It reads a `.env` file only when you name one with `env_file`, so it doesn't matter which folder your app runs from:

```python
result = evaluate_sync(transcript_text, note_text, anthropic_api_key=my_key)
result = evaluate_sync(transcript_text, note_text, env_file="config/groundline.env")
```

The argument wins over the environment, and the environment wins over `env_file`. A blank argument raises an error; it doesn't fall back to the environment or `env_file`. With no key, `evaluate_sync` raises `MissingAPIKeyError` before any paid call. Its message names an `env_file` only by its name; the error's `path` holds the full path. The key never appears in logs, errors or the result.

### Running a check

```python
from groundline_core import evaluate_sync

result = evaluate_sync(transcript_text, note_text)

print(result.flagged, result.score)  # True 0.15
for finding in result.findings:
    print(finding.severity, finding.verdict, finding.confidence, finding.statement)
print(result.counts)  # total=35 present=29 partial=3 absent=3
print(result.alarm)  # score=0.97 threshold=0.9 level='strict'
print(result.validated_preset)  # "omission_all_opus", or None if anything that affects the check was changed
print(result.usage.cost_usd)
```

For the commission check, pass its preset as the `spec`. The result has the same fields, and `counts` is `None`:

```python
from groundline_core import DEFAULT_COMMISSIONS_PRESET, evaluate_sync, load_preset

result = evaluate_sync(
    transcript_text, note_text, spec=load_preset(DEFAULT_COMMISSIONS_PRESET)
)
```

The fields mean what the API's do (see [Findings](#findings) and [Scores](#scores)), and `None` stands for the API's `null`. What the API puts in `metadata` is in separate fields in Python: `alarm`, `usage`, `pipeline` (the name of the pipeline that ran), `validated_preset` (the preset it matched) and `disclaimer` (the [Disclaimer](#disclaimer), to show with the result). `result.run` holds every step's output for inspection; its shape follows the pipeline's steps and may change without notice.

`evaluate()` is the async version, for FastAPI or other async code. Both take a `spec`, a `client` (your own `ModelClient`, such as a fake one so your app's tests cost nothing) and `anthropic_api_key` or `env_file` for the key. Pass `client` or `anthropic_api_key`, not both: a `client` brings its own key. They also accept `profile`, `cache` and `registry`, but those take internal types, so they aren't part of the public interface yet.

A spec has to make one full check: the alarm step, plus the steps one check's findings come from. Anything else raises `SpecError` before the key is looked up or a model is called.

### Errors

Every error groundline raises on purpose is a `GroundlineError`, of one of three kinds:

| Error | Raised when | What to do |
|---|---|---|
| `SetupError` | The setup stops the check: an invalid spec (`SpecError`) or a domain profile it can't load (`ProfileError`), a missing key (`MissingAPIKeyError`), a rejected key (`APIKeyRejectedError`), a model the Anthropic API doesn't have, settings that can't be read (`SettingsError`), or both `client` and `anthropic_api_key` passed. | Fix the setup. Trying again won't help. |
| `InvalidInputError` | The source or summary isn't text, or is empty or only whitespace. It's raised before the key is looked up or a model is called. | Fix the input. |
| `ModelFailureError` | Anthropic's API kept failing, the model declined, or its reply stayed unusable. `ModelTimeoutError`, a kind of `ModelFailureError`, means a time limit ran out. `step` names the step that failed, or is `None` when the whole check ran out of time between steps. | Try again later. |

```python
from groundline_core import InvalidInputError, ModelFailureError, SetupError, evaluate_sync

try:
    result = evaluate_sync(transcript_text, note_text)
except ModelFailureError as error:
    ...  # error.step is the step that failed, such as "extract"
except SetupError as error:
    ...  # error.path is the file at fault, or None
except InvalidInputError:
    ...  # the source or summary can't be checked
```

A message is safe to log or show: it never quotes the source, the summary, the key or Anthropic's own error text, and it names a file only by its name. It does quote names from a spec or profile, such as an unknown step, with anything shaped like an Anthropic or groundline API key replaced by `<redacted key>`; other kinds of secret aren't recognized. When groundline's error comes from another library's error, such as YAML's, pydantic's or the Anthropic SDK's, a traceback doesn't print that original, since its text could quote a file, a setting or the request. `SetupError.path` holds the file's full path, and the CLI prints it on a `File:` line of its own. `SpecError`, `ProfileError` and `InvalidInputError` are also `ValueError`s, but `ModelTimeoutError` isn't a `TimeoutError`.

An exception from your own `client`, or from a bug in groundline, comes out as it was raised rather than as a `GroundlineError` ([ADR 0020](https://github.com/groundline-dev/groundline/blob/main/docs/adr/0020-only-groundlines-own-failures-are-groundline-errors.md)), and it doesn't say what the check had spent. To report your model's failure as a `ModelFailureError`, your client can raise one itself.

### Public interface

Import from `groundline_core` itself. Its names, and the documented fields of the result types, keep working across upgrades. Everything else, including the modules whose names start with an underscore (such as `groundline_core._core`) and `result.run`, is internal and may change without notice.

| Name | What it is |
|---|---|
| `evaluate`, `evaluate_sync` | Run a check. |
| `load_preset`, `builtin_presets` | Load a built-in preset by name, and list the names. |
| `DEFAULT_PRESET`, `DEFAULT_COMMISSIONS_PRESET`, `CommissionPreset` | The default omission and commission presets, and the commission presets' names as a type. |
| `PipelineSpec` | A pipeline spec, as `load_preset` returns it. |
| `ModelClient`, `ModelReply` | What a `client` implements (an async `complete(prompt, *, model, effort, max_tokens)`), and what it returns. |
| `EvaluationResult`, `Finding`, `Counts`, `Alarm`, `Usage` | The result and its parts. |
| `GroundlineError`, `SetupError`, `SpecError`, `ProfileError`, `MissingAPIKeyError`, `APIKeyRejectedError`, `SettingsError`, `InvalidInputError`, `ModelFailureError`, `ModelTimeoutError` | What a check raises (see [Errors](#errors)). |
| `EvaluationType`, `Severity` | The types of `evaluation_type` (`omissions` or `commissions`) and of a finding's `severity` (`critical`, `supporting` or `peripheral`). |

The package includes its type information, so type checkers such as pyright and mypy check your code against these names.

## Using the API

Complete the [development setup](https://github.com/groundline-dev/groundline/blob/main/DEVELOPMENT.md#getting-started) first. Start the API with `make dev`; it serves at http://127.0.0.1:8000, with interactive docs at `/docs` (also at `/api`) and a read-only reference at `/redoc`. The docs list each endpoint with example requests and responses, and explain how the API is versioned. To run the API and Postgres together in Docker instead, see [Running the whole stack in Docker](https://github.com/groundline-dev/groundline/blob/main/DEVELOPMENT.md#running-the-whole-stack-in-docker); to run them as a test deployment in your AWS project, see [Deploying to AWS](https://github.com/groundline-dev/groundline/blob/main/docs/deploying-to-aws.md). Both check endpoints require a groundline API key, and every check spends your Anthropic credit. `/health` and `/ready` are public. `/health` answers 200 while the app is running, and `/ready` answers 503 while the database is unavailable.

### Request limits and timeouts

Every API route has a default request-body limit of 5 MiB, including JSON formatting and escaping. Both check endpoints also require nonblank `source` and `summary` text, with up to 200,000 characters in each field by default. Your server may use different size limits: check its [/docs](http://127.0.0.1:8000/docs) or [/api](http://127.0.0.1:8000/api) page for the current values. To change them, see [request size limits](https://github.com/groundline-dev/groundline/blob/main/DEVELOPMENT.md#request-size-limits).

By default, the server allows 120 seconds to complete either check, including authentication, model calls and waits between retries. Set your HTTP client's timeout a little higher so it also has time to send the request and receive the result. You can leave the server's timeout at its default; see [the overall check limit](https://github.com/groundline-dev/groundline/blob/main/DEVELOPMENT.md#overall-check-limit) if you need to change it.

### `POST /v1/omissions`

Runs the validated omission preset on one summary. Create a groundline API key, copy the secret printed once, and send it as a Bearer credential. This is separate from the Anthropic API key the server uses for model calls:

```sh
uv run groundline-core keys create --name "local-api"
export GROUNDLINE_API_KEY='<API key printed by keys create>'

curl -s http://127.0.0.1:8000/v1/omissions \
  -H "Authorization: Bearer $GROUNDLINE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"source": "...transcript...", "summary": "...note..."}'
```

```json
{
  "evaluation_type": "omissions",
  "flagged": true,
  "score": 0.15,
  "findings": [
    {"type": "omission", "severity": "critical", "verdict": "absent", "confidence": 0.97,
     "statement": "Allergic to penicillin. No other drug allergies.",
     "source_evidence": "penicillin, it gives me a rash",
     "summary_evidence": null, "evidence_found": true,
     "explanation": "The summary does not record this fact."}
  ],
  "counts": {"total": 35, "present": 29, "partial": 3, "absent": 3},
  "metadata": {
    "pipeline": "omission_all_opus",
    "alarm": {"score": 0.97, "threshold": 0.9, "level": "strict"},
    "usage": {"calls": 4, "input_tokens": 31620, "output_tokens": 8140, "cost_usd": 0.29},
    "disclaimer": "groundline's results help a person review a summary. …"
  }
}
```

`findings` lists the facts not fully recorded, most important first. The example is shortened: `counts` implies six findings, and it shows one. Every response has the same shape; see [Findings](#findings).

### `POST /v1/commissions`

Runs a validated commission preset on one summary. It takes the same API key, `source` and `summary` as `/v1/omissions`, and an optional `preset`, either `commission_quick` (the default) or `commission_reverse_lean`. Any other value, including `null`, gets a 422 that names the two. Both presets answer in the same shape, each flags at its own threshold, and `metadata.pipeline` names the one that ran. It answers the same [statuses](#statuses-and-errors):

```sh
curl -s http://127.0.0.1:8000/v1/commissions \
  -H "Authorization: Bearer $GROUNDLINE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"source": "...transcript...", "summary": "...note..."}'
```

```json
{
  "evaluation_type": "commissions",
  "flagged": true,
  "score": 0.1667,
  "findings": [
    {"type": "commission", "severity": "critical", "verdict": "contradicted", "confidence": 0.95,
     "statement": "Motrin 800 mg every 6 hours for a week",
     "source_evidence": "Take Motrin 600 mg every 6 hours",
     "summary_evidence": null, "evidence_found": true,
     "explanation": "The source says something different."}
  ],
  "counts": null,
  "metadata": {
    "pipeline": "commission_quick",
    "alarm": {"score": 0.95, "threshold": 0.85, "level": "default"},
    "usage": {"calls": 1, "input_tokens": 9800, "output_tokens": 420, "cost_usd": 0.0012},
    "disclaimer": "groundline's results help a person review a summary. …"
  }
}
```

`findings` lists the claims in the summary that the source doesn't support, most important first. The commissions response has the same fields as the omissions one, except that `counts` is `null`.

### Findings

Both endpoints return `evaluation_type` (`omissions` or `commissions`), `flagged`, `score`, `findings`, `counts` and `metadata`. A finding is one omission or one commission, with the same fields for either kind, so a future check that finds both can return them in one list:

| Field | Meaning |
|---|---|
| `type` | `omission` (a fact from the source that the summary leaves out or only partly records) or `commission` (a claim in the summary that the source doesn't support). |
| `severity` | `critical`, `supporting` or `peripheral`: how much it would matter if the fact were left out or the claim were wrong. `null` when the check didn't grade it. |
| `verdict` | For an omission, `partial` or `absent`. For a commission, `unsupported` (the source doesn't mention the claim) or `contradicted` (the source says otherwise). |
| `confidence` | From 0 to 1: how sure the check is that the fact is missing, or that the source doesn't support the claim. `null` when the check gave none. |
| `statement` | The fact from the source, or the words in the summary that make the claim, word for word. |
| `source_evidence` | The passage of the source behind the finding: for an omission, where the fact came from; for a contradicted commission, the passage the model quoted. `null` for an unsupported commission. |
| `summary_evidence` | For a partial omission, the words in the summary that partly record the fact. `null` otherwise. |
| `evidence_found` | Whether `source_evidence` appears in the source word for word, ignoring case, punctuation and spacing. `true` means verified. `false` only means it couldn't be matched, which a reworded quote or markup in the source, such as a scribe's `<UNSURE>` tags, can cause, so check it by eye rather than assuming it was invented. `null` when there is no evidence. |
| `explanation` | A fixed sentence for each type and verdict, with no text from the source or summary in it. Use the other fields for the details. |

`counts` is the facts found in the source and how many were present, partial or absent; it is `null` for a check that doesn't count facts, such as the commission check. `metadata` is how the check ran: `pipeline` is the validated preset, `alarm` is the alarm score, threshold and level behind the flag, and `usage` is the model calls the check made and what they cost. `metadata.disclaimer` is the [Disclaimer](#disclaimer), to show next to the result.

### Scores

`score` runs from 0 to 1, and higher is better, for both checks. A summary is flagged exactly when its score is below 0.5, and passes at 0.5 or above, so tools such as DeepEval can use their default cut of 0.5. The alarm still decides the flag: the score is the alarm score turned around and rescaled around the alarm's threshold. The CLI report and Python's `result.score` show the same score, and `--json` has it as the alarm's `rescaled_score`.

A score shows whether a critical problem was found, not how good the summary is overall, and it belongs to the preset that produced it. A commission flag means a claim wasn't found in the source, for a reviewer to check. When nothing critical is found the score is 1. That includes a source with no facts, where `counts.total` is 0 and only the extract call is charged.

### Statuses and errors

A completed check returns **200 OK**, whether or not it flags the summary ([ADR 0008](https://github.com/groundline-dev/groundline/blob/main/docs/adr/0008-the-api-returns-200-for-a-flagged-summary.md)). A flagged summary is a completed result that requires review. Other statuses indicate that the request did not produce a completed check.

Both check endpoints use the following statuses. Every error response is JSON with a `detail` field describing the error.

| Status | Meaning |
|---|---|
| 400 | The server could not decode the request body. Send JSON encoded as UTF-8. |
| 401 | The groundline API key is missing or invalid. Provide a valid, unexpired key in the `Authorization: Bearer ...` header. Revoked keys are not accepted. The response includes `WWW-Authenticate: Bearer`. |
| 404 | The route does not exist. The error names the method and path you requested; `/docs` lists the available routes. |
| 405 | The route exists, but does not accept the requested HTTP method. For example, checks require `POST`, not `GET`. |
| 413 Content Too Large | The request body exceeds the server's byte allowance, including JSON structure and escaping. The allowance applies to every HTTP path and method. Reduce its size; a declared oversized body can return this before authentication, 404 Not Found or 405 Method Not Allowed. |
| 422 | The request is invalid. Check the JSON syntax, required fields, text length and any extra fields. The body must be a JSON object, and the source and summary must not be blank. |
| 500 | An unexpected error occurred in groundline. Details are recorded in the server log. |
| 502 | The model or Anthropic's API failed, refused the request, or returned a response groundline could not use. Retrying later may help. |
| 503 | Model configuration or database authentication is unavailable. Expiry during authentication returns 503, whether the authentication limit or whole-check deadline fires first; no model work starts. |
| 504 | A model attempt or retry allowance expired, or the whole-check deadline expired after successful authentication. |

For **502 Bad Gateway** responses, `detail` identifies the step that failed and any usage recorded before it stopped. Anthropic API errors include the provider's status and error type. If a retry ends in a model error, the response is 502 even if an earlier attempt timed out.

For **503** responses, `detail` provides configuration guidance or reports that authentication is unavailable. The operator should check the [service configuration and database](https://github.com/groundline-dev/groundline/blob/main/DEVELOPMENT.md#getting-started).

**Failed checks may still incur model charges.** Interrupted model requests can incur charges without returning usage, so costs reported in errors cover only recorded usage. The provider's bill may be higher. A retry near the deadline can also use the remaining time and incur charges without producing a result.

The server enforces the body-byte allowance before JSON parsing, parses JSON before authenticating the API key, then validates the fields after authentication. This means malformed JSON returns 422 even without credentials; a body that cannot be decoded returns 400. Bodies recognized as UTF-16 or UTF-32 are decoded before JSON parsing, and may also return 422. Requests rejected by authentication never start a check or make model calls.

Error responses and server logs never include the source or summary text. Logs record each completed check's outcome, the number of omissions or commissions, and its cost, along with failures.

## Deploying to AWS

A disposable deployment runs the Compose stack on one EC2 server in your AWS project. Both access modes use HTTPS on the server's IPv4, with automatic publicly trusted certificates and no domain required. The default restricts application access to your public IP; unrestricted mode allows callers from any IP. Protected API endpoints require API keys in both modes. Both modes use synthetic test data only and delete the server and its data after eight hours by default. The tools are Terraform, the AWS CLI, `jq`, `uv`, Git and `make`; Docker is installed on the server.

```sh
make aws-up        # choose the project and the code, deploy, and check the server
make aws-destroy   # when you're done
```

[Deploying to AWS](https://github.com/groundline-dev/groundline/blob/main/docs/deploying-to-aws.md) explains each step, the settings, the costs and how to check the server.

## Pipelines, presets and profiles

- **A pipeline spec** says how to check: which steps run, which implementation does each (for example `check.llm_batch`), whether each step is on, and each step's settings, such as model, effort and thresholds. Specs are YAML or JSON. Every spec is validated before any model call, and the validation reports every problem at once.
- **A preset** is a validated spec that ships with groundline. [`omission_all_opus`](https://github.com/groundline-dev/groundline/blob/main/src/groundline_core/presets/omission_all_opus.yaml) is the default; the commission presets are [`commission_quick`](https://github.com/groundline-dev/groundline/blob/main/src/groundline_core/presets/commission_quick.yaml) and [`commission_reverse_lean`](https://github.com/groundline-dev/groundline/blob/main/src/groundline_core/presets/commission_reverse_lean.yaml). To try a variation, copy it, change it, and pass it with `--pipeline`. The report then says it isn't the validated preset, and warns if it still uses the preset's alarm thresholds, since those were calibrated only for the preset.

  Editors with YAML schema support, such as VS Code with Red Hat's YAML extension, can check a spec as you write it and suggest each implementation's settings. The preset's first line does this; in your own copy, point it at the published schema:

  ```yaml
  # yaml-language-server: $schema=https://raw.githubusercontent.com/groundline-dev/groundline/main/src/groundline_core/presets/pipeline.schema.json
  ```
- **A domain profile** says what matters in a domain: the prompts for each step and the severity rubric, in a folder with a `profile.yaml`. It can also add a disclaimer sentence to the [Disclaimer](#disclaimer). [`clinical`](https://github.com/groundline-dev/groundline/tree/main/src/groundline_core/profiles/clinical/) is built in. A new domain, such as finance, needs a new profile folder, not code changes.

## Caching and sensitive data

The library caches only when it's given a cache, and the CLI caches only with `--cache`. The cache is kept outside the repo, in your platform's cache folder (`~/Library/Caches/groundline` on macOS), or in a configured cache directory. It stores sources, summaries and the facts taken from them, so the CLI warns whenever it's on: **don't cache sensitive data**. `--json` output also contains text from the source and summary.

## Contributing

See [DEVELOPMENT.md](https://github.com/groundline-dev/groundline/blob/main/DEVELOPMENT.md) for the git hooks, running the tests (including the live smoke test and the parity test), lint and type checks, the project layout, and commit conventions. Contributions are accepted under the project's license, as section 5 of the [Apache License 2.0](https://github.com/groundline-dev/groundline/blob/main/LICENSE) sets out.

To report a vulnerability, see [SECURITY.md](https://github.com/groundline-dev/groundline/blob/main/SECURITY.md): please don't open a public issue.

## Data attribution

The test fixtures in `tests/fixtures/parity/` are rows of OmissionBench (Composo AI, CC BY 4.0), derived from PriMock57 (Babylon Health, CC BY 4.0). Full credits are in each fixture's ATTRIBUTION.md, such as [this one](https://github.com/groundline-dev/groundline/blob/main/tests/fixtures/parity/primock__day2_consultation05__omit/ATTRIBUTION.md).

## Disclaimer

groundline's results help a person review a summary. A summary that isn't flagged can still leave out or misstate important facts, so check it before relying on it. You're responsible for any decision made using groundline's results. groundline is provided "as is", and its authors accept no liability for outcomes from its use (see sections 7 and 8 of the Apache License 2.0).

Each domain profile can add a disclaimer sentence after the first one. The built-in clinical profile adds "They aren't medical advice or a clinical decision." Every result carries the full text: in `metadata.disclaimer` in an API response, in `result.disclaimer` in Python, and in the CLI's `--json` file. Each CLI report ends with the full text, and the `--help` of `groundline-core` and of its two checks ends with a short version.

## Disclaimer

groundline's results help a person review a summary. A summary that isn't flagged can still leave out or misstate important facts, so check it before relying on it. You're responsible for any decision made using groundline's results. groundline is provided "as is", and its authors accept no liability for outcomes from its use (see sections 7 and 8 of the Apache License 2.0).

Each domain profile can add a disclaimer sentence after the first one; keep it to one sentence, since it goes into every result. The built-in clinical profile adds "They aren't medical advice or a clinical decision.", which is why the example reports above end with the full text a clinical-profile run prints. Every result carries the full text: in `metadata.disclaimer` in an API response, in `result.disclaimer` in Python, and in the CLI's `--json` file. Each CLI report ends with the full text, and the `--help` of `groundline-core` and of its two checks ends with a short version.

## License

Copyright 2026 The groundline Authors. groundline is licensed under the [Apache License 2.0](https://github.com/groundline-dev/groundline/blob/main/LICENSE); see also [NOTICE](https://github.com/groundline-dev/groundline/blob/main/NOTICE). The test fixtures described under [Data attribution](#data-attribution) keep their own CC BY 4.0 license.
