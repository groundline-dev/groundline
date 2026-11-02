# groundline

## Setup

This project uses [uv](https://docs.astral.sh/uv/getting-started/installation/). After cloning, create the virtualenv, install dependencies and enable the shared git hooks:

```sh
make setup
```

`make` targets sync automatically; run `uv sync` after a pull if your editor shows missing imports. Use `uv run <command>` to run anything else inside the environment without activating it.

## Development

Start the API with auto-reload at http://127.0.0.1:8000 (interactive docs at `/docs`):

```sh
make dev
```

Run the tests:

```sh
make test
```

Run everything CI runs: [ruff](https://docs.astral.sh/ruff/), [pyright](https://microsoft.github.io/pyright/), the large-file check and the tests. This doesn't change any files:

```sh
make check
```

Apply ruff's lint fixes and formatting:

```sh
make fix
```

## Contributing

The `pre-commit` hook runs the checks in [.pre-commit-config.yaml](.pre-commit-config.yaml) on staged files. If ruff fails, run `make fix` and stage the result.

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), e.g. `feat(parser): support nested groups`.

The `commit-msg` hook adds a `Co-authored-by` line for every team member except the person committing. It recognises you by `git config user.email`, so make sure that matches your email in [.githooks/commit-msg](.githooks/commit-msg). To change the team, edit the list at the bottom of that file.
