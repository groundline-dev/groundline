"""Stand-in `aws` and `terraform` commands for testing the AWS scripts offline.

`install(bin_dir, world_path)` writes two small `aws` and `terraform` commands
into bin_dir that run this module. A test puts bin_dir first on PATH, so the
scripts in scripts/ call these instead of the real tools. Each call is
recorded in a JSON "world" file, which also holds what AWS should report: the
test writes it before the run and reads it afterwards.

They answer only the calls the scripts make, the way the real tools answer:

- `terraform output -raw NAME` prints an output until `terraform destroy`
  runs, or a destroy plan is applied, then fails as it does with no state.
  `-chdir=infra/data` acts on the data volume's folder, whose outputs, plan
  and destroyed flag are `data_outputs`, `data_plan` and `data_destroyed`; any
  other folder is infra/, with `outputs`, `plan` and `destroyed`. Applying a
  plan that isn't a destroy adds `pending_outputs` (or `data_pending_outputs`)
  to the outputs, as creating its resources would.
- `terraform plan -destroy -out=FILE` writes a plan deleting each resource the
  outputs name, which `terraform show -json FILE` prints and `terraform apply
  FILE` carries out. `terraform show -no-color FILE` prints SHORT_PLAN_SAMPLE.
- `aws ssm send-command` records the command. `aws ssm wait command-executed`
  and `aws ssm get-command-invocation` report it with the status and output of
  the first entry in `ssm_replies` whose `when` text it contains. With none, an
  upgrade reports running the commit it checks out, as the server does.
- `aws ssm get-parameter` finds the Anthropic parameter or fails with
  ParameterNotFound. With --with-decryption it fails the test run: no script
  may read the key. `aws ssm delete-parameter` deletes it, or fails with
  ParameterNotFound, or with `delete_parameter_error` when that is set.
- `aws configure get region` prints `region`, and `aws account
  get-account-information` gives `account_name` as the project's name.
- `aws ec2 describe-instances`, `describe-security-groups`, `describe-volumes`,
  `describe-addresses`, `aws iam get-role`, `get-instance-profile` and `aws
  budgets describe-budget` find what `existing` lists, or fail with that
  service's not-found error. `aws ec2 modify-instance-attribute` is recorded.
- `aws ec2 describe-instances` without --instance-ids lists `running`, each
  {"name": ..., "lifetime": ...}, as servers with those tags.
- `aws ec2 create-snapshot` adds to `snapshots` (the ids of the deployment's
  pre-upgrade snapshots), `describe-snapshots` lists them and
  `delete-snapshot` removes one. `snapshot_commits` maps an id to the short
  commit in its `groundline-pre-upgrade` tag (1234567 if not given).
  `aws ec2 wait snapshot-completed` succeeds, or fails when `snapshot_fails`
  is set. With `snapshot_lookup_error` set,
  `describe-snapshots` fails with that error code.
- An SSM command asking the server which commit it runs (`rev-parse HEAD`)
  gets `server_commit`, by default a commit the checkout doesn't know.
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
import signal
import sys
import time
from pathlib import Path
from typing import Any, cast


def flat(text: str) -> str:
    """TEXT with every run of whitespace as one space.

    The scripts wrap each message at the terminal's width when it's printed, so a
    phrase can break across lines anywhere. Look for wording in flat(output).
    """
    return " ".join(text.split())


WORLD_ENV = "FAKE_AWS_WORLD"
BOOLEAN_FLAGS = {
    "--with-decryption",
    "--no-with-decryption",
    "--no-cli-pager",
    "--overwrite",
    "--no-paginate",
    "--debug",
}


# What `terraform show -no-color PLAN` prints: a resource header, settings
# unchanged by a replacement, one changed setting that forces it, and totals.
SHORT_PLAN_SAMPLE = """Terraform will perform the following actions:

  # aws_instance.server must be replaced
-/+ resource "aws_instance" "server" {
      ~ id                 = "i-0123456789abcdef0" -> (known after apply)
        instance_type      = "t4g.small"
      ~ user_data          = "abc" -> "def" # forces replacement
    }

Plan: 1 to add, 0 to change, 1 to destroy."""


# What infra/data/ outputs for a deployment whose data volume and Elastic IP exist.
DATA_OUTPUTS = {
    "volume_id": "vol-0123456789abcdef0",
    "availability_zone": "eu-north-1a",
    "eip_allocation_id": "eipalloc-0123456789abcdef0",
    "public_ip": "1.1.1.1",
    "data_volume_gib": "5",
}


def new_world(**fields: Any) -> dict[str, Any]:
    """A world in which every resource is gone, the parameter exists and each
    SSM command succeeds with no output. Pass fields to change any of that."""
    world: dict[str, Any] = {
        "outputs": {},
        "destroyed": False,
        "data_outputs": dict(DATA_OUTPUTS),
        "data_destroyed": False,
        "calls": [],
        "commands": [],
        "ssm_replies": [],
        "parameter_exists": True,
        "region": "eu-north-1",
        "account": "123456789012",
        "account_name": "Test project",
        "existing": {},
        "running": [],
        "snapshots": [],
        "snapshot_commits": {},
        "error": None,
        "delete_parameter_error": None,
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
    chdir = next(
        (a.removeprefix("-chdir=") for a in args if a.startswith("-chdir=")), ""
    )
    args = [arg for arg in args if not arg.startswith("-chdir=")]
    if chdir.rstrip("/") == "infra/data":
        prefix, kinds, required = "data_", _DATA_OUTPUT_TYPES, ("deployment_name",)
    else:
        prefix, kinds = "", _OUTPUT_TYPES
        required = ("deployment_name", "budget_alert_email")
    outputs_key, destroyed_key, plan_key = (
        f"{prefix}outputs",
        f"{prefix}destroyed",
        f"{prefix}plan",
    )
    if args == ["output", "-json"]:
        print(json.dumps({k: {"value": v} for k, v in world[outputs_key].items()}))
        return 0
    if args[:2] == ["output", "-json"] and len(args) == 3:
        if world[destroyed_key] or args[2] not in world[outputs_key]:
            print(f'Error: Output "{args[2]}" not found', file=sys.stderr)
            return 1
        print(json.dumps(world[outputs_key][args[2]]))
        return 0
    if args[:1] == ["console"]:
        # Answers only what scripts/aws-common.sh asks: the saved app_settings,
        # which real Terraform reads from infra/terraform.tfvars.
        if "saved_app_settings" not in world:
            print("Error: the fake has no saved app_settings", file=sys.stderr)
            return 1
        print(json.dumps(json.dumps(world["saved_app_settings"])))
        return 0
    if args == ["version", "-json"]:
        print(json.dumps({"terraform_version": "1.17.1"}))
        return 0
    if args[:1] == ["plan"] and "-destroy" not in args:
        out = next(a.removeprefix("-out=") for a in args if a.startswith("-out="))
        plan = world.get(plan_key, {"variables": {}, "resource_changes": []})
        Path(out).write_text(json.dumps(plan))
        return 2 if plan["resource_changes"] else 0
    if args[:2] == ["output", "-raw"] and len(args) == 3:
        outputs: dict[str, str] = world[outputs_key]
        if world[destroyed_key] or args[2] not in outputs:
            print(f'Error: Output "{args[2]}" not found', file=sys.stderr)
            return 1
        sys.stdout.write(outputs[args[2]])
        return 0
    if args[:1] == ["destroy"]:
        world[destroyed_key] = True
        return 0
    if args[:1] == ["plan"] and "-destroy" in args:
        # Like real Terraform, fail when a required variable has no value. Real
        # Terraform reads infra/terraform.tfvars, which the tests treat as missing.
        given = {
            args[i + 1].split("=")[0]
            for i, arg in enumerate(args[:-1])
            if arg == "-var"
        }
        for name in required:
            if name not in given:
                print(
                    f'Error: No value for required variable "{name}"', file=sys.stderr
                )
                return 1
        return _destroy_plan(world, args, outputs_key, destroyed_key, kinds)
    if args[:2] == ["show", "-json"] and len(args) == 3:
        sys.stdout.write(Path(args[2]).read_text())
        return 0
    if args[:2] == ["show", "-no-color"] and len(args) == 3:
        print(SHORT_PLAN_SAMPLE)
        return 0
    if args[:1] == ["show"] and len(args) == 2:
        print("(the full Terraform plan)")
        return 0
    if args[:1] == ["apply"] and args[-1].endswith("tfplan"):
        # What the scripts keep off the screen, in the run's log.
        print(f"Applying the plan in {chdir or 'infra'}...")
        if world.get("apply_seconds") and not _apply_like_terraform(world):
            return 1
        if world.get("apply_error"):
            print(f"Error: {world['apply_error']}", file=sys.stderr)
            return 1
        plan = json.loads(Path(args[-1]).read_text())
        print(f"Apply complete! Resources: {len(plan['resource_changes'])} changed.")
        if plan.get("destroy") and plan["resource_changes"]:
            world[destroyed_key] = True
        elif plan["resource_changes"]:
            # What creating the plan's resources would add to the outputs.
            world[outputs_key].update(world.pop(f"{prefix}pending_outputs", {}))
        return 0
    if args[:1] in (["init"], ["apply"], ["version"]):
        return 0
    print(f"fake terraform: unsupported call: {args}", file=sys.stderr)
    return 2


def _apply_like_terraform(world: dict[str, Any]) -> bool:
    """Take world["apply_seconds"] to apply, stopping on SIGINT as Terraform does.

    The first SIGINT lets the change in progress finish, then stops; a second
    stops at once. Each one received is counted in world["apply_interrupts"].
    Returns whether the apply ran to the end.
    """
    world["apply_interrupts"] = 0
    deadline = time.monotonic() + float(world["apply_seconds"])

    def interrupted(signum: int, frame: object) -> None:
        nonlocal deadline
        world["apply_interrupts"] += 1
        if world["apply_interrupts"] == 1:
            print("Interrupt received. Gracefully shutting down...")
            deadline = min(deadline, time.monotonic() + 1)
        else:
            print("Two interrupts received. Exiting immediately.")
            raise SystemExit(1)

    signal.signal(signal.SIGINT, interrupted)
    while time.monotonic() < deadline:
        time.sleep(0.05)
    return world["apply_interrupts"] == 0


# The resource each output names, as `terraform show -json` gives its type.
_OUTPUT_TYPES = {
    "instance_id": "aws_instance",
    "security_group_id": "aws_security_group",
    "iam_role_name": "aws_iam_role",
    "instance_profile_name": "aws_iam_instance_profile",
    "budget_name": "aws_budgets_budget",
}
_DATA_OUTPUT_TYPES = {
    "volume_id": "aws_ebs_volume",
    "eip_allocation_id": "aws_eip",
}


def _destroy_plan(
    world: dict[str, Any],
    args: list[str],
    outputs_key: str,
    destroyed_key: str,
    kinds: dict[str, str],
) -> int:
    """Write a plan that deletes each resource the outputs name, in the shape of
    `terraform show -json`, and exit as -detailed-exitcode does: 2 for changes,
    0 for none."""
    out = next(arg.removeprefix("-out=") for arg in args if arg.startswith("-out="))
    outputs: dict[str, str] = {} if world[destroyed_key] else world[outputs_key]
    changes = [
        {
            "mode": "managed",
            "type": kind,
            "change": {"actions": ["delete"], "before": {"id": outputs[name]}},
        }
        for name, kind in kinds.items()
        if name in outputs
    ]
    plan: dict[str, Any] = {
        "variables": {},
        "resource_changes": changes,
        "destroy": True,
    }
    Path(out).write_text(json.dumps(plan))
    return 2 if changes else 0


class AWSError(Exception):
    def __init__(self, code: str, operation: str, message: str = "not found"):
        super().__init__(
            f"\nAn error occurred ({code}) when calling the {operation} "
            f"operation: {message}"
        )


def _aws(world: dict[str, Any], args: list[str]) -> int:
    if args == ["--version"]:
        print("aws-cli/2.31.0 Python/3.13.7 Darwin/25.5.0 source/arm64")
        return 0
    positional, options = _parse(args)
    if len(positional) < 2:
        return _unsupported(args)
    service, operation = positional[0], positional[1]
    # Reads the AWS CLI's own settings, so it works without signing in.
    if positional == ["configure", "get", "region"]:
        if not world["region"]:
            return 1
        print(world["region"])
        return 0
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
        case ("account", "get-account-information"):
            return {"AccountId": world["account"], "AccountName": world["account_name"]}
        case ("ssm", "send-command"):
            command_id = f"cmd-{len(world['commands']) + 1}"
            script = str(options.get("--parameters") or "")
            world["commands"].append({"id": command_id, "script": script})
            if "lifecycle" in world:
                _lifecycle_command(world, command_id, script)
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
        case ("ssm", "delete-parameter"):
            if world["delete_parameter_error"]:
                raise AWSError(
                    world["delete_parameter_error"], "DeleteParameter", "denied"
                )
            if not world["parameter_exists"]:
                raise AWSError("ParameterNotFound", "DeleteParameter", "")
            world["parameter_exists"] = False
            return {}
        case ("ssm", "put-parameter"):
            # The value has to come on stdin, never as an argument; the fake
            # keeps only how long it was, not the key itself.
            if options.get("--value") != "file:///dev/stdin":
                raise AWSError("FakeAWSRefused", "PutParameter", "key in arguments")
            if world["parameter_exists"] and "--overwrite" not in options:
                raise AWSError("ParameterAlreadyExists", "PutParameter", "")
            world["parameter_exists"] = True
            world["stored_key_length"] = len(sys.stdin.read())
            return {"Version": 1}
        case ("ec2", "describe-instances") if "--instance-ids" not in options:
            servers = [
                {
                    "Tags": [
                        {"Key": "deployment", "Value": server["name"]},
                        {"Key": "groundline-lifetime", "Value": server["lifetime"]},
                    ]
                }
                for server in world["running"]
            ]
            return {"Reservations": [{"Instances": servers}]}
        case ("ec2", "create-snapshot"):
            snapshot_id = f"snap-{len(world['snapshots']) + 1:017d}"
            world["snapshots"].append(snapshot_id)
            tags = options.get("--tag-specifications") or ""
            found = re.search(r"Key=groundline-pre-upgrade,Value=(\w+)", tags)
            if found:
                world["snapshot_commits"][snapshot_id] = found.group(1)
            return {"SnapshotId": snapshot_id, "VolumeId": options.get("--volume-id")}
        case ("ec2", "describe-snapshots"):
            if world.get("snapshot_lookup_error"):
                raise AWSError(world["snapshot_lookup_error"], "DescribeSnapshots")
            # Oldest first, each tagged with the commit it came before.
            return {
                "Snapshots": [
                    {
                        "SnapshotId": snapshot_id,
                        "StartTime": f"2026-11-17T10:{minute:02d}:00+00:00",
                        "Tags": [
                            {
                                "Key": "groundline-pre-upgrade",
                                "Value": world["snapshot_commits"].get(
                                    snapshot_id, "1234567"
                                ),
                            }
                        ],
                    }
                    for minute, snapshot_id in enumerate(world["snapshots"])
                ]
            }
        case ("ec2", "wait"):
            if world.get("snapshot_fails"):
                raise AWSError("Waiter SnapshotCompleted failed", "DescribeSnapshots")
            return ""
        case ("ec2", "delete-snapshot"):
            world["snapshots"].remove(options.get("--snapshot-id"))
            return {}
        case ("ec2", "describe-instances"):
            instance_id = options.get("--instance-ids")
            state = existing.get(str(instance_id))
            if state is None:
                raise AWSError("InvalidInstanceID.NotFound", "DescribeInstances")
            instance = {
                "InstanceId": instance_id,
                "State": {"Name": state},
                "PublicIpAddress": world.get("public_ip", "1.1.1.1"),
            }
            return {"Reservations": [{"Instances": [instance]}]}
        case ("ec2", "reboot-instances"):
            if "lifecycle" not in world:
                return None
            world["lifecycle"]["reboots"] += 1
            return {}
        case ("ec2", "describe-volumes") if "--volume-ids" not in options:
            # The data volumes make aws-destroy kept, by deployment name.
            return {
                "Volumes": [
                    {
                        "VolumeId": f"vol-kept-{name}",
                        "Tags": [
                            {"Key": "app", "Value": "groundline"},
                            {"Key": "deployment", "Value": name},
                            {"Key": "role", "Value": "data"},
                        ],
                    }
                    for name in world.get("kept_volumes", [])
                ]
            }
        case ("ec2", "describe-volumes"):
            volume_id = options.get("--volume-ids")
            if str(volume_id) not in existing:
                raise AWSError("InvalidVolume.NotFound", "DescribeVolumes")
            return {"Volumes": [{"VolumeId": volume_id}]}
        case ("ec2", "describe-addresses"):
            allocation_id = options.get("--allocation-ids")
            if str(allocation_id) not in existing:
                raise AWSError("InvalidAllocationID.NotFound", "DescribeAddresses")
            return {"Addresses": [{"AllocationId": allocation_id}]}
        case ("ec2", "modify-instance-attribute"):
            return {}
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
    if "reply" in command:
        return command.get("status", "Success"), command["reply"]
    replies: list[dict[str, str]] = world["ssm_replies"]
    for reply in replies:
        if reply["when"] in command["script"]:
            return reply.get("status", "Success"), reply.get("stdout", "")
    if "rev-parse HEAD" in command["script"]:
        return "Success", world.get("server_commit", "c" * 40) + "\n"
    # An upgrade reports the commit it checked out, as infra/server/apply does.
    upgrade = re.search(
        r"git checkout --quiet --detach '([0-9a-f]+)'", command["script"]
    )
    if upgrade:
        return "Success", f"groundline: running {upgrade.group(1)}\n"
    return "Success", ""


def _lifecycle_command(world: dict[str, Any], command_id: str, script: str) -> None:
    """SSM metadata and restart responses for the real lifecycle shell script."""
    lifecycle = world["lifecycle"]
    command = world["commands"][-1]
    assert command["id"] == command_id
    shell = json.loads(script)["commands"][0]
    if "docker compose restart caddy" in shell:
        lifecycle["restarts"] += 1
        if lifecycle.get("restart_error"):
            command.update(status="Failed", reply="")
        if lifecycle.get("change_target"):
            world["public_ip"] = "2.2.2.2"
    if "cat /proc/sys/kernel/random/boot_id" not in shell:
        return
    if lifecycle.get("ssm_error"):
        command.update(status="Failed", reply="")
        return
    restarted = lifecycle["restarts"] > 0
    rebooted = lifecycle["reboots"] > 0
    boot = "a" * 8 + "-1111-2222-3333-" + "b" * 12
    if rebooted and not lifecycle.get("unchanged_boot"):
        boot = "c" * 8 + "-1111-2222-3333-" + "d" * 12
    deadline = lifecycle.get("deadline", 10000)
    if restarted and lifecycle.get("changed_deadline"):
        deadline += 1
    volume = "groundline_caddy-data|2026-11-11T00:00:00Z"
    if restarted and lifecycle.get("replaced_volume"):
        volume = "groundline_caddy-data|2026-11-11T01:00:00Z"
    status = 1 if rebooted and lifecycle.get("failed_refresh") else 0
    startup = (
        "{ path=/opt/groundline/infra/https/refresh-address ; "
        "start_time=[Wed 2026-11-11 01:00:00 UTC] ; "
        f"code=exited ; status={status} }}"
    )
    mode = world["outputs"]["access_mode"]
    allowed = world["outputs"]["allowed_cidr"]
    if restarted and lifecycle.get("changed_access"):
        allowed = "0.0.0.0/0"
    command["reply"] = (
        f"{boot}\n{deadline}\n1000\n{volume}\n1.1.1.1\n{mode}\n{allowed}\n{startup}\n"
    )


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
