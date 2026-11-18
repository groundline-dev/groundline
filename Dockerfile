# syntax=docker/dockerfile:1

# Build stage: uv installs the locked dependencies and groundline into /app/.venv.
# The uv version in this tag must be within required-version in pyproject.toml, or the build fails.
# Base images are pinned by digest so every build starts from the same image.
# Dependabot updates digests and patch tags for both images in one PR; Python and
# uv minor upgrades are manual (see "Upgrading uv" in DEVELOPMENT.md).
FROM ghcr.io/astral-sh/uv:0.13.8-python3.13-trixie-slim@sha256:9a9f205ad2f9aaf63597afef4042c455392f9253937028264b98226e91c09518 AS builder

# Precompile bytecode for faster startup; copy rather than hard-link out of the
# cache mount; use the image's Python instead of downloading one.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first, in their own layer, so editing the source doesn't reinstall them.
# The image runs the API and the groundline-core command, so it needs both extras.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages,target=packages \
    uv sync --locked --no-dev --extra server --extra cli --no-install-project

# Then groundline itself, installed as a regular (non-editable) package, so the
# runtime stage needs only the virtualenv, not the source tree.
# packages/ holds only each workspace member's pyproject.toml (see .dockerignore):
# uv checks that the members in uv.lock exist, but installs none of them.
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY packages ./packages
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra server --extra cli --no-editable


# Runtime stage: the same Python and Debian as the build stage, without uv or build tools.
# Its Python minor (3.13) must match the build stage's tag: the virtualenv's
# interpreter link points at python3.13.
FROM python:3.13-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# The app runs from /app/.venv and never uses the system pip. Removing it drops
# pip's bundled copies of urllib3, msgpack and setuptools, which scanners flag.
# This must run before PATH puts the virtualenv first, so `python` is the system Python.
RUN useradd --create-home --uid 10001 groundline \
    && python -m pip uninstall --yes --root-user-action=ignore pip

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER groundline
WORKDIR /home/groundline

EXPOSE 8000

# The virtualenv changes with every source edit, so it comes after the settings above.
# It stays owned by root: the app user can read and run it but not modify it.
COPY --from=builder /app/.venv /app/.venv

CMD ["uvicorn", "groundline_core.app:app", "--host", "0.0.0.0", "--port", "8000"]
