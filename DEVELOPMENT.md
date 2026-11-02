# Developing groundline

How to work on groundline itself. For what it does and how to use it, start with the [README](README.md).

## Setup

Follow the README's [Setup](README.md#setup) section first: `uv sync`, and an API key in `.env` if you'll make live calls. Then enable the shared git hooks:

```sh
git config core.hooksPath .githooks
```

## Running the API

Start the API with auto-reload at http://127.0.0.1:8000 (interactive docs at `/docs`):

```sh
uv run fastapi dev
```

## Tests

Run the tests. These are offline, free, and need no API key; model calls use a scripted fake client:

```sh
uv run pytest
```

Run the live smoke test, which calls the real API on a short example (about $0.04 per run). It's excluded from the default run:

```sh
uv run pytest -m live -s
```

`tests/test_parity.py` replays a recorded run of the measured pipeline and checks that groundline sends byte-identical prompts and reaches identical results. If you change a prompt, the rubric or a step's logic, expect it to fail. That means the `omission_all_opus` preset no longer matches the measured results.

## Lint, format and type checks

Lint and format with [ruff](https://docs.astral.sh/ruff/):

```sh
uv run ruff check
uv run ruff format
```

Type-check with [pyright](https://microsoft.github.io/pyright/), in strict mode:

```sh
uv run pyright
```

## Layout

```
src/groundline/
  core/                  the pipeline framework: steps, registry, specs, runner, cache, costs
  clients/               the Anthropic client
  evaluators/omissions/  the omission check's data models, stages and report
  profiles/clinical/     the clinical profile: prompts and severity rubric
  presets/               validated pipeline specs (omission_all_opus.yaml)
  evaluate.py            evaluate(): the entry point the CLI and API share
  cli.py                 the groundline command
  app.py                 the FastAPI app
tests/                   offline tests; test_live.py is marked live
```

## Commits

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), e.g. `feat(parser): support nested groups`.

The `commit-msg` hook adds a `Co-authored-by` line for every team member except the person committing. It recognises you by `git config user.email`, so make sure that matches your email in [.githooks/commit-msg](.githooks/commit-msg). To change the team, edit the list at the bottom of that file.
