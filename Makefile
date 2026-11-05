.PHONY: setup dev db test check fix docker-build docker-run docker-smoke

setup:
	uv sync
	git config core.hooksPath .githooks

dev:
	uv run fastapi dev

# Only Postgres, from compose.yaml, for `make dev` and the database tests.
db:
	docker compose up --detach --wait postgres

test:
	uv run pytest

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
