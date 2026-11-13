"""Check what happens when an extra isn't installed (ALL-160).

Each test runs a fresh Python with some packages hidden, as if they had never
been installed: a hidden module fails to import with ModuleNotFoundError, the
same error a missing package gives.

- The library loads with neither extra.
- The groundline-core command without the cli extra prints one line naming the
  extra to install, and exits 2: the check couldn't run (ADR 0005).
- Importing the API without the server extra fails with an error naming the
  extra to install.
- A missing module that no extra provides keeps its own error, so a broken
  install isn't blamed on a missing extra.

scripts/core-install-check checks the same against a real install without
extras.
"""

import subprocess
import sys
import textwrap

import pytest

SERVER_ONLY = ["fastapi", "starlette", "uvicorn"]
CLI_ONLY = ["typer", "click", "platformdirs"]
DATABASE = ["sqlalchemy", "psycopg", "alembic"]

CLI_MESSAGE = (
    "The groundline-core command needs the cli extra: "
    'pip install "groundline-core[cli]"'
)
SERVER_MESSAGE = (
    'groundline_core.app needs the server extra: pip install "groundline-core[server]"'
)


def run_without(hidden: list[str], code: str) -> subprocess.CompletedProcess[str]:
    """Run `code` in a fresh Python where the modules in `hidden` can't be imported."""
    script = f"import sys\nfor name in {hidden!r}:\n    sys.modules[name] = None\n"
    return subprocess.run(
        [sys.executable, "-I", "-c", script + textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_library_loads_without_either_extra():
    finished = run_without(
        SERVER_ONLY + CLI_ONLY + DATABASE,
        "from groundline_core import evaluate, evaluate_sync, load_preset",
    )
    assert finished.returncode == 0, finished.stderr


def run_command(hidden: list[str], *args: str) -> subprocess.CompletedProcess[str]:
    return run_without(
        hidden,
        f"""
        sys.argv = ["groundline-core", *{list(args)!r}]
        from groundline_core.command import main
        main()
        """,
    )


@pytest.mark.parametrize("missing", ["typer", "platformdirs", "sqlalchemy"])
def test_the_command_without_the_cli_extra_names_the_extra(missing: str):
    finished = run_command([missing], "omissions", "source.txt", "summary.txt")
    assert finished.returncode == 2
    assert finished.stderr == CLI_MESSAGE + "\n"


def test_the_command_with_the_cli_extra_runs_the_cli():
    finished = run_command([], "--help")
    assert finished.returncode == 0, finished.stderr
    assert "omissions" in finished.stdout


def test_the_command_keeps_the_error_for_a_module_no_extra_provides():
    # A module of groundline's own that the command loads, standing in for any
    # failure that isn't a missing extra. (Not a core package such as yaml:
    # Python loads the package, which needs those, before main() can run.)
    finished = run_command(
        ["groundline_core.keys_cli"], "omissions", "source.txt", "summary.txt"
    )
    assert finished.returncode == 2
    assert "Traceback" in finished.stderr
    assert "keys_cli" in finished.stderr
    assert "cli extra" not in finished.stderr


def import_app(hidden: list[str]) -> subprocess.CompletedProcess[str]:
    """Import the API, printing the error's message if the import fails."""
    return run_without(
        hidden,
        """
        try:
            import groundline_core.app
        except ModuleNotFoundError as error:
            print(error)
            sys.exit(3)
        """,
    )


@pytest.mark.parametrize("missing", ["fastapi", "sqlalchemy"])
def test_the_api_without_the_server_extra_names_the_extra(missing: str):
    finished = import_app([missing])
    assert finished.returncode == 3, finished.stderr
    assert finished.stdout.strip() == SERVER_MESSAGE


def test_the_api_keeps_the_error_for_a_module_no_extra_provides():
    finished = import_app(["yaml"])
    assert finished.returncode == 3, finished.stderr
    assert "yaml" in finished.stdout
    assert "server extra" not in finished.stdout
