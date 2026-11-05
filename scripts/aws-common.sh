# shellcheck shell=bash
# Shared by the aws-* scripts, which source it after cd-ing to the repository
# root. on_server and require_running read $region and $instance, so set them
# first.
# shellcheck disable=SC2154  # region and instance come from the sourcing script

# Where the server keeps its deadline, as a Unix time (infra/bootstrap.sh.tftpl).
# shellcheck disable=SC2034  # read by the sourcing scripts
deadline_file=/var/lib/groundline/terminate-at

fail() {
  echo "error: $*" >&2
  exit 1
}

# The reminder for a server that terminated itself: terminating removes only the
# instance, its disk and its public IP, so the rest is still there.
gone_reminder() {
  echo "Terminating removed only the server and its data. Run make aws-destroy to remove" >&2
  echo "the security group, IAM role, instance profile and budget it left behind, or" >&2
  echo "make aws-deploy for a new server." >&2
}

# require_running: fail unless the instance is pending or running. A terminated
# or missing instance gets the aws-destroy reminder.
require_running() {
  local state
  if ! state="$(aws ec2 describe-instances --region "$region" --instance-ids "$instance" \
    --query 'Reservations[0].Instances[0].State.Name' --output text 2>&1)"; then
    case "$state" in
      *NotFound*) state=terminated ;;
      *) fail "couldn't check instance $instance: $state" ;;
    esac
  fi
  case "$state" in
    pending | running) ;;
    shutting-down | terminated)
      echo "error: the server ($instance) has terminated, probably because its time limit passed." >&2
      gone_reminder
      exit 1
      ;;
    *) fail "the server ($instance) is $state, not running: run make aws-deploy to replace it" ;;
  esac
}

# on_server COMMAND: run COMMAND on the server as root with SSM Run Command and
# print its output. If it fails, also print its error output, and fail.
on_server() {
  local escaped="${1//\\/\\\\}"
  escaped="${escaped//\"/\\\"}"
  local id
  id="$(aws ssm send-command --region "$region" --instance-ids "$instance" \
    --document-name AWS-RunShellScript \
    --parameters "{\"commands\":[\"$escaped\"]}" \
    --query Command.CommandId --output text)" || return 1
  local wait_status=0
  aws ssm wait command-executed --region "$region" --command-id "$id" \
    --instance-id "$instance" || wait_status=$?
  aws ssm get-command-invocation --region "$region" --command-id "$id" \
    --instance-id "$instance" --query StandardOutputContent --output text
  if [ "$wait_status" != 0 ]; then
    aws ssm get-command-invocation --region "$region" --command-id "$id" \
      --instance-id "$instance" --query StandardErrorContent --output text >&2 || true
  fi
  return "$wait_status"
}

# describe_deadline EPOCH: "18:05 EST on Thu Nov 5 (7 h 52 min from now)", in
# local time. date -r is macOS's way to format a Unix time, and date -d @ is Linux's.
describe_deadline() {
  local format='+%H:%M %Z on %a %b %-d' when left
  when="$(date -r "$1" "$format" 2>/dev/null || date -d "@$1" "$format")"
  left=$(($1 - $(date +%s)))
  if [ "$left" -le 0 ]; then
    echo "$when (it has passed; the server shuts down within 5 minutes)"
  else
    echo "$when ($((left / 3600)) h $((left % 3600 / 60)) min from now)"
  fi
}
