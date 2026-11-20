"""The wording from the first user test and the PR #48 review.

Budgets accept $1,000 and say what the alerts will do in dollars; the access
choices say who can reach the server; changing the AWS project asks first,
since it opens the browser; a deploy ends saying the deployment is up and
running; status ends with a plain verdict; destroy warns that deleting the
stored key doesn't revoke it; and the HTTPS check says what each result means.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from tests import aws_fakes
from tests.test_aws_access_modes import checkout, invoke, stub_children
from tests.test_aws_lasting import DESTROYABLE, destroy, lasting

__all__ = ["checkout"]  # the fixture, which pytest finds by its name here

ROOT = Path(__file__).resolve().parents[1]

# On main, the guess is lasting: change the budget (row 5), then Enter keeps the
# rest and the stored key.
LASTING_ANSWERS = "5\n{budget}\n\n\n"


def https_check() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "https_check", ROOT / "scripts/https_check.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_formatted_budget_is_accepted_and_said_in_dollars(checkout: Path):
    stub_children(checkout)

    result, _ = invoke(
        checkout,
        "aws-up",
        aws_fakes.new_world(),
        LASTING_ANSWERS.format(budget="$1,000"),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert 'budget_amount = "1000"' in (checkout / "infra/terraform.tfvars").read_text()
    assert (
        "$1000 a month: you'll get an email when this month's AWS costs reach $500, "
        "and again at $1000" in aws_fakes.flat(result.stdout)
    )


def test_the_default_budget_says_its_thresholds(checkout: Path):
    stub_children(checkout)

    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(), "\n\n")

    assert "reach $17.50, and again at $35" in aws_fakes.flat(result.stdout)


def test_a_budget_over_the_limit_says_the_limit(checkout: Path):
    stub_children(checkout)

    result, _ = invoke(
        checkout,
        "aws-up",
        aws_fakes.new_world(),
        LASTING_ANSWERS.format(budget="1000000\n35"),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Enter a whole number of dollars up to $99,999, such as 35." in (
        aws_fakes.flat(result.stdout)
    )
    assert 'budget_amount = "35"' in (checkout / "infra/terraform.tfvars").read_text()


def test_changing_the_project_row_asks_before_opening_the_browser(checkout: Path):
    stub_children(checkout)

    # The first run's AWS project row (1), then Enter: don't change it.
    result, world = invoke(checkout, "aws-up", aws_fakes.new_world(), "1\n\n\n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "In your browser" not in result.stdout
    assert not any(call[1:2] == ["login"] for call in world["calls"])


def test_the_access_choices_say_who_can_reach_it(checkout: Path):
    stub_children(checkout)

    # The Access row (6), keeping it, then Enter keeps the rest and the key.
    result, _ = invoke(checkout, "aws-up", aws_fakes.new_world(), "6\n\n\n\n")

    assert "1) Only this computer can reach it (IP-restricted)" in result.stdout
    assert "2) Open to any IP over HTTPS; checks need an API key (unrestricted)" in (
        aws_fakes.flat(result.stdout)
    )


def test_a_deploy_ends_saying_it_is_up_and_running(checkout: Path):
    stub_children(checkout)
    world = aws_fakes.new_world(outputs={"app_url": "https://203.0.113.7"})

    result, _ = invoke(checkout, "aws-up", world, "\n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok: production is up and running at https://203.0.113.7" in result.stdout


def test_status_with_nothing_running_ends_with_a_plain_verdict(checkout: Path):
    lasting(checkout)

    result, _ = invoke(checkout, "aws-status", aws_fakes.new_world())

    assert result.returncode != 0
    assert result.stderr.endswith("Needs attention: see above.\n\n")


def test_destroy_warns_that_deleting_the_key_does_not_revoke_it(checkout: Path):
    lasting(checkout, lifetime="disposable")
    world = aws_fakes.new_world(
        outputs=dict(DESTROYABLE, lifetime="disposable"),
        existing={"i-test": "terminated"},
    )

    result, _ = destroy(checkout, world, "1\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "warning: deleting it from AWS doesn't revoke your Anthropic API key. Anyone "
        "with access to this AWS project could have copied it while it was stored."
        in aws_fakes.flat(result.stderr)
    )


def test_certificate_times_read_as_dates():
    module = https_check()
    assert module.readable_time("Nov 25 14:10:38 2026 GMT") == (
        "Nov 25, 2026 at 14:10:38 UTC"
    )
    assert module.readable_time("Jan  5 01:02:03 2027 GMT") == (
        "Jan 5, 2027 at 01:02:03 UTC"
    )
    assert module.readable_time("tomorrow") == "tomorrow"
