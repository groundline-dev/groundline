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
6. **Alarm:** flag the summary when a critical fact is confidently missing. The alarm score is the highest missing-confidence among critical facts still flagged, with partials at half weight.

Every step uses Claude Opus 5.5, so the pipeline can run under Anthropic's BAA once it's in production. On 50 held-out notes from [OmissionBench](https://huggingface.co/datasets/ComposoAI/OmissionBench), with thresholds fixed in advance, it caught 16/50 omissions with 1/50 clean notes flagged, at about $0.28 per note. Those results hold only for the unchanged `omission_all_opus` preset. The report tells you whether a run used it.

The default commission pipeline, the **quick commission check**, runs three steps:

1. **Commissions check:** one Claude Haiku 5.5 call lists the summary's claims that the source doesn't support. Each has the summary's own words, whether the source contradicts it or just doesn't say it, a 0–100 confidence that it's unsupported, and how much it would matter if it were wrong. A contradicted claim also gets the passage of the source that contradicts it.
2. **Commissions refute:** a second look at the critical flags. It can only clear a flag, and only with a verified quote from the source. It is switched off in the preset.
3. **Alarm:** flag the summary when a critical claim is confidently unsupported. The alarm score is the highest unsupported confidence among critical claims still flagged, and the one threshold level, `default`, is 0.85.

Measured on 172 held-out commission pairs (pre-registered): 151 caught, 1/110 clean notes flagged, about $0.002 per note. Those results hold only for the unchanged `commission_quick` preset. On realistic notes outside that benchmark, it flagged about a third. A blind review found most of those flags were real: exam findings or details the transcript doesn't say aloud, which a transcript can't tell apart from invented content. Treat a flag as something to look at, not proof of invention. The quick check uses Claude Haiku 5.5, not Opus. Anthropic's BAA is understood to cover services and features rather than specific models, but confirm that before production (see [ADR 0007](docs/adr/0007-commission-check-ships-quick.md)).

## Setup

This project uses [uv](https://docs.astral.sh/uv/getting-started/installation/). After cloning, create the virtualenv and install dependencies:

```sh
uv sync
```

Run `uv sync` again whenever `uv.lock` changes after a pull. Use `uv run <command>` to run things inside the environment without activating it.

Model calls need an Anthropic API key. Copy `.env.example` to `.env` and add your key, or export `ANTHROPIC_API_KEY`. `.env` is git-ignored; never commit a key. groundline reads `.env` from the folder you run it in, so run commands from the repo root. A missing or rejected key produces an error saying where groundline looked and how to fix it, before any paid call.

**Live calls cost money.** A typical clinical note costs about $0.28 for the omission check and about $0.002 for the commission check. Agree a budget with your team before running anything at scale.

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

**Exit codes:**
- **0:** checked, not flagged.
- **1:** checked and flagged.
- **2:** couldn't check: bad arguments or files, an invalid pipeline or profile, a missing or rejected API key, a failed stage, an unwritable `--json` path, or an unexpected error. The command prints a one-line `error:` message, and if a stage failed after making model calls, what those calls cost.

**Options:**

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

Cost              $0.00 · 1 calls · 347 input / 40 output tokens · 4.1 s
```

The exit codes and options are the same as for omissions. `--threshold` names an alarm level in the pipeline, and the preset's only level is `default` (0.85). If a pipeline switches refute on, the report adds a `Cleared by refute` line.

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
for issue in result.run.output("commissions_refute", CommissionsRefuteOutput).issues:
    print(issue.severity, issue.verdict, issue.confidence, issue.quote)
```

`evaluate()` is the async version, for FastAPI or other async code. Both accept a `spec`, `profile`, `cache` or `client` to override the defaults.

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

See [DEVELOPMENT.md](DEVELOPMENT.md) for the git hooks, running the tests (including the live smoke test and the parity test), lint and type checks, the project layout, and commit conventions.

## Data attribution

The test fixture in `tests/fixtures/parity/` is a row of OmissionBench (Composo AI, CC BY 4.0), derived from PriMock57 (Babylon Health, CC BY 4.0). Full credits are in its [ATTRIBUTION.md](tests/fixtures/parity/primock__day2_consultation05__omit/ATTRIBUTION.md).
