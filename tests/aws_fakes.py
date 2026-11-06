"""Stand-in `aws` and `terraform` commands for testing the AWS scripts offline.

`install(bin_dir, world_path)` writes two small `aws` and `terraform` commands
into bin_dir that run this module. A test puts bin_dir first on PATH, so the
scripts in scripts/ call these instead of the real tools. Each call is
recorded in a JSON "world" file, which also holds what AWS should report: the
test writes it before the run and reads it afterwards.

They answer only the calls the scripts make, the way the real tools answer:

- `terraform output -raw NAME` prints an output until `terraform destroy`
  runs, or a destroy plan is applied, then fails as it does with no state.
- `terraform plan -destroy -out=FILE` writes a plan deleting each resource the
  outputs name, which `terraform show -json FILE` prints and `terraform apply
  FILE` carries out.
- `aws ssm send-command` records the command. `aws ssm wait command-executed`
  and `aws ssm get-command-invocation` report it with the status and output of
  the first entry in `ssm_replies` whose `when` text it contains.
- `aws ssm get-parameter` finds the Anthropic parameter or fails with
  ParameterNotFound. With --with-decryption it fails the test run: no script
  may read the key.
- `aws ec2 describe-instances`, `describe-security-groups`, `aws iam get-role`,
  `get-instance-profile` and `aws budgets describe-budget` find what `existing`
  lists, or fail with that service's not-found error.
- With `error` set, every aws call fails with that error code, as with an
  expired sign-in.

--query handles dotted paths with [0] and [] (the subset of JMESPath the
scripts need), and --output text prints the result as the AWS CLI does. Any
other call fails with "fake aws: unsupported call", so a script change that
needs more shows up as a test failure instead of a silent pass.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, cast

WORLD_ENV = "FAKE_AWS_WORLD"
BOOLEAN_FLAGS = {
    "--with-decryption",
    "--no-with-decryption",
    "--no-cli-pager",
    "--no-paginate",
    "--debug",
}


def new_world(**fields: Any) -> dict[str, Any]:
    """A world in which every resource is gone, the parameter exists and each
    SSM command succeeds with no output. Pass fields to change any of that."""
    world: dict[str, Any] = {
        "outputs": {},
        "destroyed": False,
        "calls": [],
        "commands": [],
        "ssm_replies": [],
        "parameter_exists": True,
        "account": "123456789012",
        "existing": {},
        "error": None,
    }
    world.update(fields)
    return world


def install(bin_dir: Path, world_path: Path) -> dict[str, str]:
    """Write the fake commands into bin_dir; return the environment they need."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    for tool in ("aws", "terraform"):
        command = bin_dir / tool
        command.write_text(
            "#!/bin/sh\n"
            f'PYTHONPATH="{root}" exec "{sys.executable}" -m tests.aws_fakes '
            f'{tool} "$@"\n'
        )
        command.chmod(0o755)
    return {WORLD_ENV: str(world_path)}


def main(argv: list[str]) -> int:
    path = Path(os.environ[WORLD_ENV])
    world: dict[str, Any] = json.loads(path.read_text())
    tool, args = argv[0], argv[1:]
    world["calls"].append([tool, *args])
    try:
        if tool == "terraform":
            return _terraform(world, args)
        return _aws(world, args)
    finally:
        path.write_text(json.dumps(world, indent=2))


def _terraform(world: dict[str, Any], args: list[str]) -> int:
    args = [arg for arg in args if not arg.startswith("-chdir=")]
    if args[:2] == ["output", "-raw"] and len(args) == 3:
        outputs: dict[str, str] = world["outputs"]
        if world["destroyed"] or args[2] not in outputs:
            print(f'Error: Output "{args[2]}" not found', file=sys.stderr)
            return 1
        sys.stdout.write(outputs[args[2]])
        return 0
    if args[:1] == ["destroy"]:
        world["destroyed"] = True
        return 0
    if args[:1] == ["plan"] and "-destroy" in args:
        return _destroy_plan(world, args)
    if args[:2] == ["show", "-json"] and len(args) == 3:
        sys.stdout.write(Path(args[2]).read_text())
        return 0
    if args[:1] == ["show"] and len(args) == 2:
        print("(the full Terraform plan)")
        return 0
    if args[:1] == ["apply"] and args[-1].endswith("tfplan"):
        if json.loads(Path(args[-1]).read_text())["resource_changes"]:
            world["destroyed"] = True
        return 0
    if args[:1] in (["init"], ["apply"], ["version"]):
        return 0
    print(f"fake terraform: unsupported call: {args}", file=sys.stderr)
    return 2


# The resource each output names, as `terraform show -json` gives its type.
_OUTPUT_TYPES = {
    "instance_id": "aws_instance",
    "security_group_id": "aws_security_group",
    "iam_role_name": "aws_iam_role",
    "instance_profile_name": "aws_iam_instance_profile",
    "budget_name": "aws_budgets_budget",
}


def _destroy_plan(world: dict[str, Any], args: list[str]) -> int:
    """Write a plan that deletes each resource the outputs name, in the shape of
    `terraform show -json`, and exit as -detailed-exitcode does: 2 for changes,
    0 for none."""
    out = next(arg.removeprefix("-out=") for arg in args if arg.startswith("-out="))
    outputs: dict[str, str] = {} if world["destroyed"] else world["outputs"]
    changes = [
        {
            "mode": "managed",
            "type": kind,
            "change": {"actions": ["delete"], "before": {"id": outputs[name]}},
        }
        for name, kind in _OUTPUT_TYPES.items()
        if name in outputs
    ]
    plan: dict[str, Any] = {"variables": {}, "resource_changes": changes}
    Path(out).write_text(json.dumps(plan))
    return 2 if changes else 0


class AWSError(Exception):
    def __init__(self, code: str, operation: str, message: str = "not found"):
        super().__init__(
            f"\nAn error occurred ({code}) when calling the {operation} "
            f"operation: {message}"
        )


def _aws(world: dict[str, Any], args: list[str]) -> int:
    positional, options = _parse(args)
    if len(positional) < 2:
        return _unsupported(args)
    service, operation = positional[0], positional[1]
    try:
        if world["error"]:
            raise AWSError(world["error"], operation, "the request failed")
        if service == "ssm" and operation == "wait":
            return _ssm_wait(world, positional, options)
        reply = _reply(world, service, operation, options)
    except AWSError as error:
        print(error, file=sys.stderr)
        return 254
    if reply is None:
        return _unsupported(args)
    _print(reply, options)
    return 0


def _parse(args: list[str]) -> tuple[list[str], dict[str, str | None]]:
    positional: list[str] = []
    options: dict[str, str | None] = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg.startswith("--"):
            name, _, inline = arg.partition("=")
            if inline:
                options[name] = inline
            elif name in BOOLEAN_FLAGS or index + 1 == len(args):
                options[name] = None
            else:
                options[name] = args[index + 1]
                index += 1
        else:
            positional.append(arg)
        index += 1
    return positional, options


def _unsupported(args: list[str]) -> int:
    print(f"fake aws: unsupported call: {args}", file=sys.stderr)
    return 2


def _reply(
    world: dict[str, Any],
    service: str,
    operation: str,
    options: dict[str, str | None],
) -> Any:
    existing: dict[str, Any] = world["existing"]
    match (service, operation):
        case ("sts", "get-caller-identity"):
            return {"Account": world["account"], "Arn": "arn:aws:iam::x:user/me"}
        case ("ssm", "send-command"):
            command_id = f"cmd-{len(world['commands']) + 1}"
            script = str(options.get("--parameters") or "")
            world["commands"].append({"id": command_id, "script": script})
            return {"Command": {"CommandId": command_id, "Status": "Pending"}}
        case ("ssm", "get-command-invocation"):
            status, output = _invocation(world, options.get("--command-id"))
            return {
                "CommandId": options.get("--command-id"),
                "Status": status,
                "StatusDetails": status,
                "ResponseCode": 0 if status == "Success" else 1,
                "StandardOutputContent": output,
                "StandardErrorContent": "",
            }
        case ("ssm", "get-parameter"):
            if "--with-decryption" in options:
                raise AWSError(
                    "FakeAWSRefused",
                    "GetParameter",
                    "a script tried to read the Anthropic API key",
                )
            name = options.get("--name")
            if not world["parameter_exists"]:
                raise AWSError("ParameterNotFound", "GetParameter", "")
            return {"Parameter": {"Name": name, "Type": "SecureString"}}
        case ("ec2", "describe-instances"):
            instance_id = options.get("--instance-ids")
            state = existing.get(str(instance_id))
            if state is None:
                raise AWSError("InvalidInstanceID.NotFound", "DescribeInstances")
            instance = {"InstanceId": instance_id, "State": {"Name": state}}
            return {"Reservations": [{"Instances": [instance]}]}
        case ("ec2", "describe-security-groups"):
            group_id = options.get("--group-ids")
            if str(group_id) not in existing:
                raise AWSError("InvalidGroup.NotFound", "DescribeSecurityGroups")
            return {"SecurityGroups": [{"GroupId": group_id}]}
        case ("iam", "get-role"):
            name = options.get("--role-name")
            if str(name) not in existing:
                raise AWSError("NoSuchEntity", "GetRole")
            return {"Role": {"RoleName": name}}
        case ("iam", "get-instance-profile"):
            name = options.get("--instance-profile-name")
            if str(name) not in existing:
                raise AWSError("NoSuchEntity", "GetInstanceProfile")
            return {"InstanceProfile": {"InstanceProfileName": name}}
        case ("budgets", "describe-budget"):
            name = options.get("--budget-name")
            if str(name) not in existing:
                raise AWSError("NotFoundException", "DescribeBudget")
            return {"Budget": {"BudgetName": name}}
        case _:
            return None


def _invocation(world: dict[str, Any], command_id: str | None) -> tuple[str, str]:
    commands: list[dict[str, str]] = world["commands"]
    command = next((c for c in commands if c["id"] == command_id), None)
    if command is None:
        raise AWSError("InvocationDoesNotExist", "GetCommandInvocation", "")
    replies: list[dict[str, str]] = world["ssm_replies"]
    for reply in replies:
        if reply["when"] in command["script"]:
            return reply.get("status", "Success"), reply.get("stdout", "")
    return "Success", ""


def _ssm_wait(
    world: dict[str, Any], positional: list[str], options: dict[str, str | None]
) -> int:
    if positional[2:] != ["command-executed"]:
        return _unsupported(positional)
    status, _ = _invocation(world, options.get("--command-id"))
    if status != "Success":
        print(
            "\nWaiter CommandExecuted failed: Waiter encountered a terminal "
            'failure state: For expression "Status" we matched expected path: '
            f'"{status}"',
            file=sys.stderr,
        )
        return 255
    return 0


def _print(reply: Any, options: dict[str, str | None]) -> None:
    query = options.get("--query")
    if query is not None:
        reply = _query(reply, query)
    if options.get("--output") == "text":
        print(_text(reply))
    else:
        print(json.dumps(reply, indent=4))


def _query(value: Any, query: str) -> Any:
    """Follow a dotted path with [0] and [] steps, as JMESPath does."""
    for part in re.findall(r"[^.\[\]]+|\[\d*\]", query):
        if part == "[]":
            value = [inner for item in _items(value) for inner in _as_list(item)]
        elif part.startswith("["):
            value = _items(value)[int(part[1:-1])]
        elif isinstance(value, list):
            value = [item[part] for item in _items(value)]
        else:
            value = value[part]
    return value


def _items(value: Any) -> list[Any]:
    return cast(list[Any], value) if isinstance(value, list) else []


def _as_list(value: Any) -> list[Any]:
    return _items(value) if isinstance(value, list) else [value]


def _text(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, list):
        return "\t".join(_text(item) for item in _items(value))
    return str(value)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
