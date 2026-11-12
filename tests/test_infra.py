"""Check the AWS deployment files for what `terraform test` can't see.

The Terraform tests in infra/tests/ plan against a mock AWS provider, which
doesn't apply the provider's default tags and can't list every resource of a
kind. These tests read the files instead:

- A resource isn't tagged project=groundline, so a forgotten one is hard to
  find in the AWS console.
- A Region is written into the Terraform files, so a contributor whose AWS
  project is in another Region can't deploy.
- Terraform state is kept somewhere other than this computer.
- Terraform creates or reads the Anthropic parameter, so the API key lands in
  Terraform state.
- The server's role gets an AWS managed policy, which can read every parameter.
- A second inbound rule exposes ports besides HTTPS.
- A resource isn't tagged with its deployment, so developers sharing an AWS
  project can't tell whose it is.
- Terraform state or a contributor's settings can be committed.

They also check that the make targets run the deployment scripts and that
docs/deploying-to-aws.md documents them. They only read files, so they don't need
Terraform or AWS.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
BOOTSTRAP = INFRA / "bootstrap.sh.tftpl"


def terraform_files() -> dict[str, str]:
    """Each infra/*.tf file's name and text, leaving out the tests in infra/tests/."""
    files = {path.name: path.read_text() for path in sorted(INFRA.glob("*.tf"))}
    assert files, "infra/ has no .tf files"
    return files


def all_terraform() -> str:
    return "\n".join(terraform_files().values())


def blocks(kind: str, label: str) -> list[str]:
    """The bodies of every top-level `kind "label" "name" { ... }` block."""
    pattern = rf'^{kind}\s+"{label}"\s+"\w+"\s*\{{(.*?)^\}}'
    return re.findall(pattern, all_terraform(), re.MULTILINE | re.DOTALL)


def test_every_resource_is_tagged_app_groundline():
    """The provider's default_tags tag every resource it creates. The key is app,
    since AWS Settings uses "project" for the AWS account itself."""
    assert re.search(
        r'default_tags\s*\{\s*tags\s*=\s*\{[^}]*\bapp\s*=\s*"groundline"',
        all_terraform(),
    ), 'the aws provider should set default_tags { tags = { app = "groundline" } }'


def test_every_resource_is_tagged_with_its_deployment():
    """Developers share an AWS project, so each resource says whose it is."""
    assert re.search(
        r"default_tags\s*\{\s*tags\s*=\s*\{[^}]*\bdeployment\s*=\s*var\.deployment_name",
        all_terraform(),
    ), "the aws provider's default_tags should set deployment = var.deployment_name"


AWS_REGION = (
    r"\b(?:us|eu|ap|ca|sa|me|af|il|mx)-(?:gov-|iso-)?"
    r"(?:north|south|east|west|central)(?:east|west)?-\d\b"
)
"""An AWS Region name such as eu-north-1, us-gov-west-1 or ap-southeast-2."""


def test_no_region_is_written_into_the_terraform_files():
    """The Region comes from the region variable, since each AWS project has its own."""
    regions = {
        name: found
        for name, text in terraform_files().items()
        if (found := re.findall(AWS_REGION, text))
    }
    assert not regions, f"Regions written into {regions}"


def test_terraform_state_stays_on_this_computer():
    text = all_terraform()

    assert not re.search(r'\bbackend\s+"', text), "a backend block moves the state"
    assert not re.search(r"^\s*cloud\s*\{", text, re.MULTILINE)


def test_terraform_never_creates_or_reads_the_anthropic_parameter():
    """The parameter is created with the AWS CLI, so the key stays out of state.
    Reading the AMI ID from AWS's public parameter is fine."""
    assert not blocks("resource", "aws_ssm_parameter")
    for body in blocks("data", "aws_ssm_parameter"):
        assert "anthropic" not in body.lower(), body


def test_the_instance_role_gets_no_managed_policy():
    """AmazonSSMManagedInstanceCore can read every parameter. The role gets
    only its own inline policy, which the Terraform tests check."""
    text = all_terraform()

    assert "AmazonSSMManagedInstanceCore" not in text
    assert not blocks("resource", "aws_iam_role_policy_attachment")
    assert not blocks("resource", "aws_iam_policy_attachment")
    assert "managed_policy_arns" not in text


def test_only_the_server_decrypts_the_anthropic_parameter():
    """The scripts may check that the parameter exists, never read its value."""
    assert BOOTSTRAP.is_file(), "infra/bootstrap.sh.tftpl is missing"
    assert "--with-decryption" in BOOTSTRAP.read_text()
    readers = [
        path.name
        for path in (ROOT / "scripts").iterdir()
        if path.is_file() and "--with-decryption" in path.read_text()
    ]
    assert not readers, f"{readers} read the Anthropic API key"


def test_the_security_group_has_one_inbound_rule():
    """Public TCP 443 allows TLS validation; Caddy restricts application access."""
    assert len(blocks("resource", "aws_vpc_security_group_ingress_rule")) == 1
    assert not blocks("resource", "aws_security_group_rule")
    assert not re.search(r"^\s*ingress\s*\{", all_terraform(), re.MULTILINE)


@pytest.mark.parametrize(
    "path",
    [
        "infra/terraform.tfstate",
        "infra/terraform.tfstate.backup",
        "infra/.terraform/providers/x",
        "infra/terraform.tfvars",
    ],
)
def test_terraform_state_and_settings_are_not_committed(path: str):
    """State and terraform.tfvars hold the instance's details and an email address."""
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", path], cwd=ROOT
    )
    assert ignored.returncode == 0, f"git doesn't ignore {path}"


@pytest.mark.parametrize(
    "path", ["infra/.terraform.lock.hcl", "infra/terraform.tfvars.example"]
)
def test_the_lock_file_and_example_settings_are_committed(path: str):
    assert (ROOT / path).is_file(), f"{path} is missing"
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", path], cwd=ROOT
    )
    assert ignored.returncode == 1, f"git ignores {path}"


@pytest.mark.parametrize(
    ("target", "command"),
    [
        ("aws-up", "scripts/aws-up"),
        ("aws-deploy", "scripts/aws-deploy"),
        ("aws-smoke", "scripts/aws-smoke-test"),
        ("aws-smoke-live", "scripts/aws-smoke-test --live"),
        ("aws-key", "scripts/aws-key"),
        ("aws-status", "scripts/aws-status"),
        ("aws-reset-deadline", "scripts/aws-reset-deadline"),
        ("aws-destroy", "scripts/aws-destroy"),
    ],
)
def test_each_make_target_runs_its_script(target: str, command: str):
    printed = subprocess.run(
        ["make", "--dry-run", target], cwd=ROOT, capture_output=True, text=True
    )

    assert printed.returncode == 0, printed.stderr
    assert re.search(rf"^{re.escape(command)}\b(?! --live)", printed.stdout, re.M), (
        printed.stdout
    )


def test_the_deploying_guide_documents_every_command_and_setting():
    text = (ROOT / "docs" / "deploying-to-aws.md").read_text()

    for mention in [
        "make aws-up",
        "make aws-deploy",
        "make aws-smoke",
        "make aws-smoke-live",
        "make aws-key",
        "make aws-status",
        "make aws-reset-deadline",
        "make aws-destroy",
        "/groundline/<deployment_name>/anthropic-api-key",
        "terraform.tfvars",
        "Region",
        "groundline keys create",
    ]:
        assert mention in text, f"docs/deploying-to-aws.md doesn't mention {mention}"


def test_development_md_points_to_the_deploying_guide():
    text = (ROOT / "DEVELOPMENT.md").read_text()
    section = re.search(r"^## Deploying to AWS\n(.*?)(?=^## |\Z)", text, re.M | re.S)

    assert section, 'DEVELOPMENT.md has no "## Deploying to AWS" section'
    assert "(docs/deploying-to-aws.md)" in section[1]
    assert "make aws-up" in section[1]
