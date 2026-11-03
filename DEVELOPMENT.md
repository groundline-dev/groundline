# Developing groundline

How to work on groundline itself. For what it does and how to use it, start with the [README](README.md).

## Setup

Follow the README's [Setup](README.md#setup) section first: `make setup`, and an API key in `.env` if you'll make live calls. `make setup` installs the dependencies and enables the shared git hooks in [.githooks/](.githooks/).

## Running the API

Start the API with auto-reload at http://127.0.0.1:8000 (interactive docs at `/docs`):

```sh
make dev
```

The README's [Using the API](README.md#using-the-api) describes the endpoints. Each endpoint has a request and response model of its own in `src/groundline/api/`, so refactoring the pipeline's models can't change the API by accident.

## Tests

Run the tests. These are offline, free, and need no API key; model calls use a scripted fake client:

```sh
make test
```

Run the live smoke tests, which call the real Anthropic API on short examples: the omission preset, the commission preset and the `/v1/omissions` endpoint (about $0.09 per run in all). They're excluded from the default run:

```sh
uv run pytest -m live -s
```

`tests/test_parity.py` (omissions) and `tests/test_parity_commissions.py` (commissions) each replay a recorded run of a measured pipeline and check that groundline sends byte-identical prompts and reaches identical results. If you change a prompt, the rubric or a step's logic, expect them to fail. That means the `omission_all_opus` or `commission_quick` preset no longer matches the measured results.

`tests/test_commission_prompts.py` also pins both commission prompts to omission-pipeline's versions by hash, including the refute prompt, which the preset leaves switched off and so no parity run exercises.

## Editor schemas

Editors check the YAML files against JSON schemas, which each file names on its first line:

- profiles (`profile.yaml`) against [profile.schema.json](src/groundline/profiles/profile.schema.json), generated from the profile model;
- pipeline specs and presets against [pipeline.schema.json](src/groundline/presets/pipeline.schema.json), generated from the registry, so each stage's settings are checked against its implementation. A setting's docstring becomes its description.

After changing the profile model, adding an implementation, or changing an implementation's settings, regenerate them (a test fails until you do):

```sh
uv run python -m groundline.profiles.schema
uv run python -m groundline.presets.schema
```

## Lint, format and type checks

Run everything CI runs: [ruff](https://docs.astral.sh/ruff/), [pyright](https://microsoft.github.io/pyright/) in strict mode, the large-file check and the tests. This doesn't change any files:

```sh
make check
```

Apply ruff's lint fixes and formatting:

```sh
make fix
```

The `pre-commit` hook runs the checks in [.pre-commit-config.yaml](.pre-commit-config.yaml) on staged files. If ruff fails, run `make fix` and stage the result. Pyright errors need fixing by hand; run `uv run pyright` to check types on their own.

## Layout

```
src/groundline/
  core/                    the pipeline framework: steps, registry, specs, runner, cache, costs
  clients/                 the Anthropic client
  evaluators/              what the checks share: severity, the alarm's output, report helpers
  evaluators/omissions/    the omission check's data models, stages and report
  evaluators/commissions/  the commission check's data models, stages and report
  profiles/clinical/       the clinical profile: prompts and severity rubric
  presets/                 validated pipeline specs (omission_all_opus, commission_quick)
  evaluate.py              evaluate(): the entry point the CLI and API share
  cli.py                   the groundline command
  api/                     the API's endpoints, with their own request and response models
  app.py                   the FastAPI app
tests/                     offline tests; test_live.py is marked live
```

## Vocabulary and decisions

[CONTEXT.md](CONTEXT.md) is the glossary: the project's words for things, and the words to avoid. [docs/adr/](docs/adr/) records the decisions that would surprise a newcomer, and why they were made. Read both before changing the design, and update them as you go: the `/grill-with-docs` skill from the Skills For Real Engineers plugin does this during a design session.

## Commits

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), e.g. `feat(parser): support nested groups`.

The `commit-msg` hook adds a `Co-authored-by` line for every team member except the person committing. It recognises you by `git config user.email`, so make sure that matches your email in [.githooks/commit-msg](.githooks/commit-msg). To change the team, edit the list at the bottom of that file.
