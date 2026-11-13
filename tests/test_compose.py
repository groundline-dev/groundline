"""Check compose.yaml for mistakes the smoke test can't catch.

Each of these leaves the stack working, so scripts/docker-smoke-test still
passes:

- The Postgres image isn't pinned to a sha256 digest, so the image behind its
  tag can change between pulls.
- A port is published on every network interface, so other machines on the
  network can reach the app or the database. It should be on 127.0.0.1 only.
- Postgres lets clients connect without a password.
- compose.yaml reads a setting that .env.example doesn't list (the API size
  limits are left out on purpose).
- The app container gets the admin password, or misses a setting the app reads.

These tests only read the file, so they don't need Docker.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from groundline.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.yaml"
ENV_EXAMPLE = ROOT / ".env.example"


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    assert COMPOSE.is_file(), "compose.yaml is missing"
    return yaml.safe_load(COMPOSE.read_text())["services"]


def with_defaults(value: str) -> str:
    """Replace each ${NAME:-default} with its default, as when .env doesn't set NAME."""
    return re.sub(r"\$\{\w+:-([^}]*)\}", r"\1", value)


def test_postgres_is_version_18_pinned_by_digest(services: dict[str, Any]):
    assert re.fullmatch(
        r"postgres:18[\w.-]*@sha256:[0-9a-f]{64}", services["postgres"]["image"]
    )


def test_the_database_lives_in_a_named_volume_at_the_18_path(
    services: dict[str, Any],
):
    """Postgres 18 images keep their data in /var/lib/postgresql/18/docker, so the
    named volume mounts at /var/lib/postgresql."""
    volumes: dict[str, Any] = yaml.safe_load(COMPOSE.read_text()).get("volumes") or {}
    mounts = [str(m).split(":") for m in services["postgres"].get("volumes", [])]

    assert any(
        source in volumes and target == "/var/lib/postgresql"
        for source, target, *_ in mounts
    )


@pytest.mark.parametrize("service", ["postgres", "app"])
def test_ports_are_published_only_on_this_computer_by_default(
    services: dict[str, Any], service: str
):
    ports = services[service].get("ports", [])

    assert ports, f"{service} publishes no port"
    for port in ports:
        assert with_defaults(str(port)).startswith("127.0.0.1:"), port


def test_postgres_publishes_its_port_on_loopback_always(services: dict[str, Any]):
    """APP_BIND_ADDRESS can open the app to other machines. Postgres has no such
    setting, so its port stays on 127.0.0.1 whatever .env says."""
    for port in services["postgres"]["ports"]:
        assert str(port).startswith("127.0.0.1:"), port


def test_postgres_checks_passwords(services: dict[str, Any]):
    environment: Any = services["postgres"].get("environment") or {}

    assert "POSTGRES_HOST_AUTH_METHOD" not in str(environment)
    assert "POSTGRES_PASSWORD" in str(environment)


def test_setup_waits_for_a_healthy_postgres_and_the_app_for_setup(
    services: dict[str, Any],
):
    assert "pg_isready" in str(services["postgres"]["healthcheck"]["test"])
    setup_needs = services["setup"]["depends_on"]
    app_needs = services["app"]["depends_on"]

    assert setup_needs["postgres"]["condition"] == "service_healthy"
    assert app_needs["setup"]["condition"] == "service_completed_successfully"


def test_restart_policies(services: dict[str, Any]):
    """Setup exits when it finishes, so it restarts only on failure.
    unless-stopped would start it again every time it exits."""
    assert services["postgres"]["restart"] == "unless-stopped"
    assert services["app"]["restart"] == "unless-stopped"
    assert services["setup"]["restart"] == "on-failure"


def test_the_app_health_check_ignores_the_database(services: dict[str, Any]):
    """A database outage must not mark the app unhealthy."""
    check = str(services["app"]["healthcheck"]["test"])

    assert "/health" in check
    assert "/ready" not in check


@pytest.mark.usefixtures("services")  # fails plainly if compose.yaml is missing
def test_compose_settings_other_than_api_limits_are_in_env_example():
    names = set(re.findall(r"\$\{(\w+)", COMPOSE.read_text())) - API_LIMIT_SETTINGS
    documented = ENV_EXAMPLE.read_text()

    missing = {
        name
        for name in names
        if not re.search(rf"^#?\s*{name}=", documented, re.MULTILINE)
    }
    assert not missing, f".env.example doesn't mention {sorted(missing)}"


ADMIN_ONLY = {"DATABASE_ADMIN_URL", "POSTGRES_PASSWORD"}
API_LIMIT_SETTINGS = {
    "GROUNDLINE_MAX_BODY_BYTES",
    "GROUNDLINE_MAX_SOURCE_CHARS",
    "GROUNDLINE_MAX_SUMMARY_CHARS",
}


def environment_names(service: dict[str, Any]) -> set[str]:
    """Return the names a service's `environment:` sets, as a list or a mapping."""
    environment: Any = service.get("environment") or {}
    items: list[Any] = list(environment)  # a list's entries, or a mapping's names
    return {str(item).split("=", 1)[0] for item in items}


@pytest.mark.parametrize("service", ["setup", "app"])
def test_no_container_gets_the_whole_env_file(services: dict[str, Any], service: str):
    """env_file would pass a container all of .env, admin password included.
    Each container gets only the variables its `environment:` lists."""
    assert "env_file" not in services[service]


def test_the_app_gets_every_setting_it_reads_and_not_the_admin_password(
    services: dict[str, Any],
):
    """A setting added to Settings must also be passed to the app in compose.yaml,
    or the app runs in Compose without it and nothing reports the gap."""
    reads = {
        str(field.validation_alias or name.upper())
        for name, field in Settings.model_fields.items()
    } - ADMIN_ONLY
    gets = environment_names(services["app"])

    assert reads <= gets, f"compose.yaml doesn't pass {sorted(reads - gets)} to app"
    assert not gets & ADMIN_ONLY


def test_setup_gets_both_database_urls(services: dict[str, Any]):
    assert {"DATABASE_URL", "DATABASE_ADMIN_URL"} <= environment_names(
        services["setup"]
    )


@pytest.mark.parametrize("name", ["DATABASE_URL", "DATABASE_ADMIN_URL"])
def test_the_example_urls_follow_the_postgres_port(name: str):
    """These URLs reach Postgres from this computer through POSTGRES_HOST_PORT,
    so changing that port in .env must change them too."""
    line = re.search(rf"^{name}=(.*)$", ENV_EXAMPLE.read_text(), re.MULTILINE)

    assert line, f".env.example has no {name}"
    assert "@localhost:${POSTGRES_HOST_PORT:-5433}/" in line[1]


@pytest.mark.parametrize(
    "name",
    [
        "GROUNDLINE_CHECK_TIMEOUT_SECONDS",
        "GROUNDLINE_MODEL_MAX_ATTEMPTS",
    ],
)
def test_deadline_and_retry_overrides_are_optional(services: dict[str, Any], name: str):
    environment = services["app"].get("environment", {})
    assert name in environment, f"app must forward {name} explicitly"
    assert environment[name] is None, "Compose must not provide an app default"
    example = ENV_EXAMPLE.read_text()
    assert re.search(rf"^#\s*{name}=", example, re.MULTILINE)
    assert not re.search(rf"^{name}=", example, re.MULTILINE)


def test_compose_exposes_only_operator_controls(
    services: dict[str, Any],
):
    names = {
        name
        for name in services["app"]["environment"]
        if name.startswith("GROUNDLINE_")
    }
    assert (
        names
        == {
            "GROUNDLINE_CHECK_TIMEOUT_SECONDS",
            "GROUNDLINE_MODEL_MAX_ATTEMPTS",
        }
        | API_LIMIT_SETTINGS
    )


@pytest.mark.parametrize("name", sorted(API_LIMIT_SETTINGS))
def test_api_limits_forward_overrides_without_compose_defaults(
    services: dict[str, Any], name: str
):
    environment = services["app"]["environment"]
    assert environment.get(name) == "${" + name + ":-}"
    assert not re.search(rf"^#?\s*{name}=", ENV_EXAMPLE.read_text(), re.MULTILINE)
