.PHONY: setup dev db test-fast test-db test-live check fix docker-build docker-run docker-smoke \
	deploy aws-smoke aws-smoke-live destroy

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

# The AWS test server (see DEVELOPMENT.md, "Deploying to AWS"). It costs money
# while it runs: make destroy when you're done.
deploy:
	scripts/aws-deploy

# Checks the server from here without calling Claude.
aws-smoke:
	scripts/aws-smoke-test

# Also sends one real commission check, which costs under a cent.
aws-smoke-live:
	scripts/aws-smoke-test --live

destroy:
	scripts/aws-destroy
