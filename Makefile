.PHONY: setup dev db test-fast test-api test-db test-live check fix docker-build docker-run docker-smoke

setup:
	uv sync
	git config core.hooksPath .githooks

dev:
	uv run fastapi dev

# Only Postgres, from compose.yaml, for `make dev` and the database tests.
db:
	docker compose up --detach --wait postgres

# The tests that need nothing running and cost nothing (make check runs them too).
test-fast:
	uv run pytest

# Offline API scenarios; optional pytest flags include --junitxml=PATH.
test-api:
	uv run pytest -v tests/test_health.py $(wildcard tests/test_api_*.py) $(PYTEST_ARGS)

# The database tests, against the Postgres from compose.yaml (started first).
test-db: db
	uv run pytest -m db

# The live tests call the real Anthropic API and cost money.
test-live:
	uv run pytest -m live -s

# Read-only: the same checks the pre-commit hook and CI run, plus the tests.
check:
	uv run pre-commit run --all-files
	uv run pytest

fix:
	uv run ruff check --fix
	uv run ruff format

# The app image, built from the Dockerfile (see DEVELOPMENT.md).
IMAGE ?= groundline:dev

docker-build:
	docker build --tag $(IMAGE) .

# The whole stack from compose.yaml: Postgres, setup, then the app.
docker-run:
	docker compose up --build

# Build the image, then start the Compose stack and smoke-test it.
docker-smoke: docker-build
	scripts/docker-smoke-test
