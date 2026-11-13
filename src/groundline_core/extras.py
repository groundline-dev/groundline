"""What to say when an extra isn't installed.

A plain install of groundline-core has only what the pipelines need. The API
needs the server extra and the groundline-core command needs the cli extra.
Without one, loading them fails on a missing package; these turn that failure
into a line that says what to install.
"""

SERVER_PACKAGES = frozenset(
    {"fastapi", "starlette", "uvicorn", "sqlalchemy", "psycopg", "alembic"}
)
"""The packages the server extra installs, as their import names."""

CLI_PACKAGES = frozenset(
    {"typer", "click", "platformdirs", "sqlalchemy", "psycopg", "alembic"}
)
"""The packages the cli extra installs, as their import names."""

SERVER_MESSAGE = (
    'groundline_core.app needs the server extra: pip install "groundline-core[server]"'
)
CLI_MESSAGE = (
    "The groundline-core command needs the cli extra: "
    'pip install "groundline-core[cli]"'
)


def is_missing_one_of(error: ModuleNotFoundError, packages: frozenset[str]) -> bool:
    """Whether `error` is about one of `packages` (or a module inside one) not
    being installed."""
    return (error.name or "").partition(".")[0] in packages
