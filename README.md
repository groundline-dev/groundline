# groundline

## Setup

This project uses [uv](https://docs.astral.sh/uv/getting-started/installation/). After cloning, create the virtualenv and install dependencies:

```sh
uv sync
```

Run `uv sync` again whenever `uv.lock` changes after a pull. Use `uv run <command>` to run things inside the environment without activating it.

Then enable the shared git hooks:

```sh
git config core.hooksPath .githooks
```

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), e.g. `feat(parser): support nested groups`.

The `commit-msg` hook adds a `Co-authored-by` line for every team member except the person committing. It recognises you by `git config user.email`, so make sure that matches your email in [.githooks/commit-msg](.githooks/commit-msg). To change the team, edit the list at the bottom of that file.
