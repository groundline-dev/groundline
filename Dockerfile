# syntax=docker/dockerfile:1

# Build stage: uv installs the locked dependencies and groundline into /app/.venv.
# The uv version in this tag must be within required-version in pyproject.toml, or the build fails.
# Base images are pinned by digest so every build starts from the same image.
# Dependabot updates digests and patch tags for both images in one PR; Python and
# uv minor upgrades are manual (see "Upgrading uv" in DEVELOPMENT.md).
FROM ghcr.io/astral-sh/uv:0.13.7-python3.13-trixie-slim@sha256:6ff812e34a0e14b4b8bc9ddf25e77b3e16bac6e0c4d9b67641f40366fa382d4c AS builder

# Precompile bytecode for faster startup; copy rather than hard-link out of the
# cache mount; use the image's Python instead of downloading one.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first, in their own layer, so editing the source doesn't reinstall them.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# Then groundline itself, installed as a regular (non-editable) package, so the
# runtime stage needs only the virtualenv, not the source tree.
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


# Runtime stage: the same Python and Debian as the build stage, without uv or build tools.
# Its Python minor (3.13) must match the build stage's tag: the virtualenv's
# interpreter link points at python3.13.
FROM python:3.13-slim-trixie@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1

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

CMD ["uvicorn", "groundline.app:app", "--host", "0.0.0.0", "--port", "8000"]
