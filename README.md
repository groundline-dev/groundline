# groundline

groundline checks an AI-written summary against its source. Its **omission check** reports which important facts the summary leaves out, how much each one matters, and whether the summary should be flagged. Its **commission check** (often called hallucination detection) reports which statements in the summary the source doesn't support, either because it contradicts them or doesn't mention them. Its first domain is clinical notes checked against the consultation transcript.

**Status:** early development. It is research code, **not for clinical use**, and must not be used with real patient data yet (see [Caching and sensitive data](#caching-and-sensitive-data)).

## How it works

The default pipeline, the **all-Opus omission check**, runs six steps:

1. **Extract:** list the facts a complete summary should record, each with a quote from the source.
2. **Rank:** grade each fact critical, supporting or peripheral, using the domain's severity rubric.
3. **Check:** one call decides, for every fact, whether the summary records it (present, partial or absent), with a 0–100 confidence that it's missing.
4. **Refute:** a second look at flagged facts. It can only clear a flag, and only with a verified quote from the summary.
5. **Score:** counts and ratios of what's missing.
6. **Alarm:** flag the summary when a critical fact is confidently missing. The alarm score is the highest missing confidence among critical facts still flagged, with partials at half weight.

Every step uses Claude Opus 5.5, so the pipeline can run under Anthropic's BAA once it's in production. On 50 held-out notes from [OmissionBench](https://huggingface.co/datasets/ComposoAI/OmissionBench), with thresholds fixed in advance, it caught 16/50 omissions with 1/50 clean notes flagged, at about $0.28 per note. Those results hold only for the unchanged `omission_all_opus` preset. The report tells you whether a run used it.

The default commission pipeline, the **quick commission check**, runs three steps:

1. **Commissions check:** one Claude Haiku 5.5 call lists the summary's claims that the source doesn't support. Each has the summary's own words, whether the source contradicts it or just doesn't say it, a 0–100 confidence that it's unsupported, and how much it would matter if it were wrong. A contradicted claim also gets the passage of the source that contradicts it.
2. **Commissions refute:** a second look at the critical flags. It can only clear a flag, and only with a verified quote from the source. It is switched off in the preset.
3. **Alarm:** flag the summary when a critical claim is confidently unsupported. The alarm score is the highest unsupported confidence among critical claims still flagged, and the one threshold level, `default`, is 0.85.

Measured on 172 held-out commission pairs (pre-registered), it caught 151, flagged 1 of 110 clean notes, and cost about $0.002 per note. Those results hold only for the unchanged `commission_quick` preset.

On realistic notes outside that benchmark, it flagged about a third of them. A blind review found that most of those flags were on genuine content the transcript doesn't say aloud, such as exam findings, which the check can't tell apart from invented content. A flag means the note needs a look; it isn't proof that anything was invented.

The quick check uses Claude Haiku 5.5, not Opus. Anthropic's BAA is understood to cover services and features rather than specific models, but confirm that before production (see [ADR 0007](docs/adr/0007-commission-check-ships-quick.md)).

## Setup

This project uses [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13 (0.13.7 or later); check with `uv --version`. After cloning, create the virtualenv, install dependencies and enable the shared git hooks:

```sh
make setup
```

`make` targets sync automatically; run `uv sync` after a pull if your editor shows missing imports. Use `uv run <command>` to run anything else inside the environment without activating it.

Model calls need an Anthropic API key. Copy `.env.example` to `.env` and add your key, or export `ANTHROPIC_API_KEY`. groundline stores its own API-key hashes and metadata in PostgreSQL. HTTP checks require PostgreSQL for authentication; CLI and Python checks do not. [DEVELOPMENT.md](DEVELOPMENT.md#getting-started) explains how to set it up. `.env` is git-ignored; never commit a key. groundline reads `.env` from the folder you run it in, so run commands from the repo root. A missing or rejected Anthropic API key produces an error saying where groundline looked and how to fix it, before any paid call.

**Live calls cost money.** A typical clinical note costs about $0.28 for the omission check and about $0.002 for the commission check. Agree a budget with your team before running anything at scale.

## Managing API keys

These are groundline API keys for callers of protected HTTP endpoints, separate from the Anthropic API key used for model calls. Set `DATABASE_URL` and complete the [database setup](DEVELOPMENT.md#getting-started) first; these commands access PostgreSQL directly and do not require the API server to be running or an Anthropic API key.

```sh
uv run groundline keys create
uv run groundline keys create --name "Avi's laptop" --expires-in 90d
uv run groundline keys list
uv run groundline keys revoke <key-id>
```

Creation prints the name, full UUID identifier, expiry, and plaintext API key **once, after the database commit succeeds**. Copy the secret then: only its SHA-256 hash, a short display prefix, and metadata are stored. If terminal output fails after creation, the API key may already exist; use the identifier in the error to revoke it and create a replacement.

- Without `--name`, a friendly label such as `nimble-otter-a7c91e2b` is generated. Custom names must be non-empty, printable on one line, and at most 128 characters after trimming surrounding spaces. Existing names are unchanged. Names may repeat; revocation uses the full identifier, not the name or secret.
- Without `--expires-in`, the API key never expires. Lifetimes are positive whole numbers followed by `s`, `m`, `h`, or `d`, such as `30m`, `12h`, or `90d`. Expiry is measured from creation and displayed in UTC.
- Revocation is permanent. Repeating it succeeds without changing the original revocation timestamp. An unknown identifier is an error. Commands exit 0 on success and 2 on error; errors go to stderr.

`keys list` shows every API key newest first in aligned columns, with its full UUID, name, identifying prefix, status, and UTC expiry (`never` when unset). Values are not truncated. It includes revoked and expired keys; an active key past its expiry displays as `expired` without changing its stored status. No credentials or hashes are shown. An empty database prints `No API keys found.` Prefixes help identify a key but are not guaranteed unique: use the full UUID to revoke it.

Callers send `Authorization: Bearer <api-key>` to a protected route. Authentication checks the primary database on every request, so revocation affects subsequent checks across workers and app instances; requests already authorized may finish. Invalid, revoked, or expired API keys receive 401; unavailable authentication storage produces 503. `/health` and `/ready` remain public. The reusable dependency is available as described in [DEVELOPMENT.md](DEVELOPMENT.md#protecting-an-endpoint). `POST /v1/omissions` and `POST /v1/commissions` use this dependency. Missing API keys also receive 401; all credential rejections use `{"detail": "Invalid or missing API key"}` and `WWW-Authenticate: Bearer`.

## Checking for omissions

```sh
uv run groundline omissions transcript.txt note.txt
```

Example report:

```
Omission check   transcript.txt · note.txt
Pipeline         omission_all_opus · clinical profile (validated preset)
Result           FLAGGED (alarm 0.97 ≥ 0.90, strict)

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
```

### Exit codes

- `0`: checked, not flagged.
- `1`: checked and flagged.
- `2`: couldn't check: bad arguments or files, an invalid pipeline or profile, a missing or rejected Anthropic API key, a failed stage, an unwritable `--json` path, or an unexpected error. The command prints a one-line `error:` message, and if a stage failed after making model calls, what those calls cost.

### Options

| Option | What it does |
|---|---|
| `--threshold strict\|moderate` | Which alarm level to flag at. `strict` (0.90, the default) flags fewer clean summaries; `moderate` (0.425) catches more omissions but flags more clean summaries. Both were fixed in advance, so either keeps the run on the validated preset. |
| `--pipeline SPEC.yaml` | Run a custom pipeline spec instead of the preset. |
| `--profile DIR` | Use a domain profile folder instead of the pipeline's built-in profile. |
| `--cache` | Cache each step's output so identical re-runs are free. Off by default; see below. |
| `--json PATH` | Also write the full result as JSON: every step's output, call records, usage and the flag decision. |

## Checking for commissions

```sh
uv run groundline commissions transcript.txt note.txt
```

Example report:

```
Commission check  transcript.txt · note.txt
Pipeline          commission_quick · clinical profile (validated preset)
Result            FLAGGED (alarm 0.95 ≥ 0.85, default)

Not supported by the source (severity order):
  severity    verdict       confidence  claim
  critical    contradicted  0.95        Motrin 800 mg every 6 hours for a week
                                        source: "take motrin 600 mg every 6 hours for a week"
  supporting  unsupported   0.70        neurovascular examination intact

Cost              $0.0020 · 1 calls · 14,820 input / 1,040 output tokens · 4.1 s
```

The [exit codes](#exit-codes) and [options](#options) are the same as for omissions. `--threshold` names an alarm level in the pipeline, and the preset's only level is `default` (0.85). If the model's quote of the source passage isn't found word for word in the source, the report still shows it, marked `(not found word for word in the source)`. If a pipeline switches refute on, the report adds a `Cleared by refute` line.

## Using it from Python

```python
from groundline import evaluate_sync
from groundline.evaluators.models import AlarmOutput
from groundline.evaluators.omissions.models import ScoreOutput

result = evaluate_sync(transcript_text, note_text)

alarm = result.run.output("alarm", AlarmOutput)
print(alarm.flagged, alarm.score)
for fact in result.run.output("score", ScoreOutput).missing_facts:
    print(fact.severity, fact.verdict, fact.fact)
print(result.validated_preset)  # "omission_all_opus", or None if anything was changed
print(result.run.usage.cost_usd)
```

For the commission check, pass its preset as the `spec`:

```python
from groundline import DEFAULT_COMMISSIONS_PRESET, evaluate_sync, load_preset
from groundline.evaluators.commissions.models import CommissionsRefuteOutput
from groundline.evaluators.models import AlarmOutput

result = evaluate_sync(
    transcript_text, note_text, spec=load_preset(DEFAULT_COMMISSIONS_PRESET)
)

print(result.run.output("alarm", AlarmOutput).flagged)
refute = result.run.output("commissions_refute", CommissionsRefuteOutput)
for c in refute.commissions:
    print(c.severity, c.support_verdict, c.unsupported_confidence, c.quote)
```

`evaluate()` is the async version, for FastAPI or other async code. Both accept a `spec`, `profile`, `cache`, `client` or `registry` to override the defaults.

## Using the API

Set up [PostgreSQL](DEVELOPMENT.md#getting-started) and configure `ANTHROPIC_API_KEY` first; `make dev` reads `DATABASE_URL` from `.env`. Start the API with `make dev`; it serves at http://127.0.0.1:8000, with interactive docs at `/docs` (also at `/api`) and a read-only reference at `/redoc`. The docs list each endpoint with example requests and responses, and explain how the API is versioned. To run the API and Postgres together in Docker instead, see [Running the whole stack in Docker](DEVELOPMENT.md#running-the-whole-stack-in-docker); to run them as a test deployment in your AWS project, see [Deploying to AWS](docs/deploying-to-aws.md). Both check endpoints require a groundline API key, and every check spends your Anthropic credit. `/health` and `/ready` are public. `/health` answers 200 while the app is running, and `/ready` answers 503 while the database is unavailable.

### `POST /v1/omissions`

Runs the validated omission preset on one summary. Create a groundline API key, copy the secret printed once, and send it as a Bearer credential. This is separate from the Anthropic API key the server uses for model calls:

```sh
uv run groundline keys create --name "local-api"
export GROUNDLINE_API_KEY='<API key printed by keys create>'

curl -s http://127.0.0.1:8000/v1/omissions \
  -H "Authorization: Bearer $GROUNDLINE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"source": "...transcript...", "summary": "...note...", "threshold": "strict"}'
```

`threshold` is optional: `strict` (the default) or `moderate`, as for the CLI. `source` and `summary` must not be blank, and each can be up to 200,000 characters. A check takes about 30 to 60 seconds, so give your HTTP client a long timeout.

```json
{
  "flagged": true,
  "alarm": {"score": 0.97, "threshold": 0.9, "level": "strict"},
  "omissions": [
    {"fact": "Allergic to penicillin. No other drug allergies.", "severity": "critical",
     "verdict": "absent", "missing_confidence": 0.97, "quote": null}
  ],
  "counts": {"total": 35, "present": 29, "partial": 3, "absent": 3},
  "pipeline": "omission_all_opus",
  "validated_preset": "omission_all_opus",
  "usage": {"calls": 4, "input_tokens": 31620, "output_tokens": 8140, "cost_usd": 0.29}
}
```

`omissions` lists the facts not fully recorded, most important first.

### `POST /v1/commissions`

Runs the validated quick commission preset on one summary. It takes the same API key, `source` and `summary` as `/v1/omissions`, but no `threshold`, since the preset has a single alarm level. It answers the same [statuses](#statuses-and-errors):

```sh
curl -s http://127.0.0.1:8000/v1/commissions \
  -H "Authorization: Bearer $GROUNDLINE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"source": "...transcript...", "summary": "...note..."}'
```

```json
{
  "flagged": true,
  "alarm": {"score": 0.95, "threshold": 0.85, "level": "default"},
  "commissions": [
    {"claim": "Motrin 800 mg every 6 hours for a week", "severity": "critical",
     "support_verdict": "contradicted", "unsupported_confidence": 0.95,
     "evidence": "take motrin 600 mg every 6 hours for a week", "evidence_found": true}
  ],
  "pipeline": "commission_quick",
  "validated_preset": "commission_quick",
  "usage": {"calls": 1, "input_tokens": 9800, "output_tokens": 420, "cost_usd": 0.0012}
}
```

`commissions` lists the claims in the summary that the source doesn't support, most important first. `support_verdict` is `contradicted` (the source says otherwise) or `unsupported` (the source doesn't mention it). For a contradicted claim, `evidence` is the passage of the source the model quoted; it is `null` for an unsupported one. `evidence_found` says whether that passage appears in the source word for word, ignoring case, punctuation and spacing. `true` means verified. `false` only means it couldn't be matched, which a reworded quote or markup in the source, such as a scribe's `<UNSURE>` tags, can cause, so check it by eye rather than assuming it was invented.

### Statuses and errors

Every finished check answers **200, flagged or not** ([ADR 0008](docs/adr/0008-the-api-returns-200-for-a-flagged-summary.md)). Other statuses mean the check couldn't finish. They apply to both endpoints except where marked omissions-only:

| Status | Meaning |
|---|---|
| 400 | The body isn't valid UTF-8 text. (Bytes that begin like UTF-16 or UTF-32 text are read as that and answer 422 if they aren't JSON.) |
| 401 | The groundline API key is missing, malformed, unknown, revoked, or expired. Includes `WWW-Authenticate: Bearer`. |
| 404 | No such route. `detail` names the method and path, and points to `/docs`. |
| 405 | The route doesn't take that method, such as `GET /v1/omissions`. |
| 422 | The request is unusable: broken JSON, not a JSON object, a blank or too-long text, an unexpected field, or (omissions only) an unknown `threshold`. |
| 500 | An unexpected error, a bug in groundline. Details are in the server log. |
| 502 | The model or Anthropic's API failed: an unusable reply, a refusal or an outage. `detail` names the step and what the check had spent; for an Anthropic API error it gives only the status and error type. Retrying later may work. |
| 503 | `ANTHROPIC_API_KEY` is missing or rejected, or settings cannot be read. It's also 503 when authentication is unavailable (missing/unusable `DATABASE_URL` or unavailable storage), with `{"detail": "Authentication is unavailable"}`. Model setup errors provide setup guidance, without the path of the server's `.env`. |

JSON parsing happens before authentication: broken JSON returns 422 and invalid UTF-8 bodies return 400 even without credentials. For readable JSON, authentication precedes field validation. Requests rejected by authentication never start the pipeline or make model calls.

Every error answers with JSON: `{"detail": ...}`. Error responses and the server log never contain text from the source or summary. The log records each finished check's outcome, number of omissions or commissions, and cost, and each failure.

## Deploying to AWS

A disposable deployment runs the Compose stack on one EC2 server in your AWS project. New configurations recommend public HTTPS on the server's IPv4, with automatic publicly trusted certificates and no domain required. Existing configurations retain tester-only HTTP unless explicitly changed. Both modes use synthetic test data only and delete the server and its data after eight hours by default. The tools are Terraform, the AWS CLI, `jq`, `uv`, Git and `make`; Docker is installed on the server.

```sh
make aws-up        # choose the project and the code, deploy, and check the server
make aws-destroy   # when you're done
```

[Deploying to AWS](docs/deploying-to-aws.md) explains each step, the settings, the costs and how to check the server.

## Pipelines, presets and profiles

- **A pipeline spec** says how to check: which steps run, which implementation does each (for example `check.llm_batch`), whether each step is on, and each step's settings, such as model, effort and thresholds. Specs are YAML or JSON. Every spec is validated before any model call, and the validation reports every problem at once.
- **A preset** is a validated spec that ships with groundline. [`omission_all_opus`](src/groundline/presets/omission_all_opus.yaml) is the default. To try a variation, copy it, change it, and pass it with `--pipeline`. The report then says it isn't the validated preset, and warns if it still uses the preset's alarm thresholds, since those were calibrated only for the preset.

  Editors with YAML schema support, such as VS Code with Red Hat's YAML extension, can check a spec as you write it and suggest each implementation's settings. The preset's first line does this; in your own copy, point it at the published schema:

  ```yaml
  # yaml-language-server: $schema=https://raw.githubusercontent.com/groundline-dev/groundline/main/src/groundline/presets/pipeline.schema.json
  ```
- **A domain profile** says what matters in a domain: the prompts for each step and the severity rubric, in a folder with a `profile.yaml`. [`clinical`](src/groundline/profiles/clinical/) is built in. A new domain, such as finance, needs a new profile folder, not code changes.

## Caching and sensitive data

The library caches only when it's given a cache, and the CLI caches only with `--cache`. The cache is kept outside the repo, in your platform's cache folder (`~/Library/Caches/groundline` on macOS), or in `GROUNDLINE_CACHE_DIR` if set. It stores sources, summaries and the facts taken from them, so the CLI warns whenever it's on: **don't cache sensitive data**. `--json` output also contains text from the source and summary.

## Contributing

See [DEVELOPMENT.md](DEVELOPMENT.md) for the git hooks, running the tests (including the live smoke test and the parity test), lint and type checks, the project layout, and commit conventions. Contributions are accepted under the project's license, as section 5 of the [Apache License 2.0](LICENSE) sets out.

## Data attribution

The test fixtures in `tests/fixtures/parity/` are rows of OmissionBench (Composo AI, CC BY 4.0), derived from PriMock57 (Babylon Health, CC BY 4.0). Full credits are in each fixture's ATTRIBUTION.md, such as [this one](tests/fixtures/parity/primock__day2_consultation05__omit/ATTRIBUTION.md).

## License

Copyright 2026 The groundline Authors. groundline is licensed under the [Apache License 2.0](LICENSE); see also [NOTICE](NOTICE). The test fixtures described under [Data attribution](#data-attribution) keep their own CC BY 4.0 license.
