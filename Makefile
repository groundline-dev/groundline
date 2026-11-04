.PHONY: setup dev test check fix docker-build docker-run docker-smoke

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

# The app in Docker, built from the Dockerfile (see DEVELOPMENT.md).
IMAGE ?= groundline:dev
# The port on your machine; the app inside the container always listens on 8000.
# 8001, not 8000, so it can run alongside `make dev`.
DOCKER_PORT ?= 8001

docker-build:
	docker build --tag $(IMAGE) .

# .env, if present, is mounted read-only where the app looks for it (its working
# folder), so the app reads it the same way as under `make dev`. --env-file would
# keep quotes around values as part of the value.
docker-run:
	docker run --rm --publish 127.0.0.1:$(DOCKER_PORT):8000 $(if $(wildcard .env),--volume "$(CURDIR)/.env:/home/groundline/.env:ro") $(IMAGE)

# Build the image, start it and check that /health returns 200.
docker-smoke: docker-build
	scripts/docker-smoke-test $(IMAGE)
