.PHONY: setup dev db test-fast test-db test-live check fix docker-build docker-run docker-smoke \
	infra-check aws-deploy aws-smoke aws-smoke-live aws-destroy

setup:
	uv sync
	git config core.hooksPath .githooks

dev:
	uv run fastapi dev

# Only Postgres, from compose.yaml, for `make dev` and the database tests.
db:
	docker compose up --detach --wait postgres

# Every test except those for test-db and test-live, with fakes in place of
# Postgres and Claude. Needs nothing running, costs nothing; make check runs it too.
test-fast:
	uv run pytest

# The database tests, against the Postgres from compose.yaml (started first).
test-db: db
	uv run pytest -m db

# The live tests call the real Anthropic API and cost money.
test-live:
	uv run pytest -m live -s

# Read-only: lint, types, the large-file check and the tests that need nothing
# running. CI also runs test-db, docker-smoke and infra-check.
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

# Formatting, validation and tests of the Terraform in infra/, against a mock AWS
# provider: no AWS credentials, nothing created. Needs Terraform; CI runs it.
infra-check:
	terraform -chdir=infra fmt -check -recursive
	terraform -chdir=infra init -input=false
	terraform -chdir=infra validate
	terraform -chdir=infra test

# Your AWS test deployment (see DEVELOPMENT.md, "Deploying to AWS"). It costs money
# while it runs: make aws-destroy when you're done.
aws-deploy:
	scripts/aws-deploy

# Checks the server from here without calling Claude.
aws-smoke:
	scripts/aws-smoke-test

# Also sends one real commission check, which costs under a cent.
aws-smoke-live:
	scripts/aws-smoke-test --live

aws-destroy:
	scripts/aws-destroy
