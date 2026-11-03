.PHONY: setup dev test check fix

setup:
	uv sync
	git config core.hooksPath .githooks

dev:
	uv run fastapi dev

test:
	uv run pytest

# Read-only: the same checks the pre-commit hook and CI run, plus the tests.
check:
	uv run pre-commit run --all-files
	uv run pytest

fix:
	uv run ruff check --fix
	uv run ruff format
