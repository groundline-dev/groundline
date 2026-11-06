# shellcheck shell=bash
# Shared by the aws-* scripts, which source it after cd-ing to the repository
# root. on_server and require_running read $region and $instance, so set them
# first.
# shellcheck disable=SC2154  # region and instance come from the sourcing script

# Where the server keeps its deadline, as a Unix time (infra/bootstrap.sh.tftpl).
# shellcheck disable=SC2034  # read by the sourcing scripts
deadline_file=/var/lib/groundline/terminate-at

# Colour and bold only where the output goes to a terminal, not to a file or a log.
if [ -t 1 ]; then
  bold=$'\033[1m' green=$'\033[32m' reset=$'\033[0m'
else
  bold="" green="" reset=""
fi
if [ -t 2 ]; then
  red=$'\033[31m' yellow=$'\033[33m' reset_err=$'\033[0m'
else
  red="" yellow="" reset_err=""
fi

ok() { echo "${green}ok:${reset} $*"; }
warn() { echo "${yellow}warning:${reset_err} $*" >&2; }
error() { echo "${red}error:${reset_err} $*" >&2; }
fail() {
  error "$*"
  exit 1
}

# step TEXT: a heading for one part of a longer run, such as make aws-up's steps.
step() { printf '\n%s\n' "${bold}$*${reset}"; }

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
      error "the server ($instance) has terminated, probably because its time limit passed."
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

# friendly_name TYPE: what plan summaries call a Terraform resource type.
friendly_name() {
  case "$1" in
    aws_security_group) echo "security group" ;;
    aws_vpc_security_group_ingress_rule | aws_vpc_security_group_egress_rule) echo "firewall rules" ;;
    aws_iam_role) echo "IAM role" ;;
    aws_iam_role_policy) echo "IAM policy" ;;
    aws_iam_instance_profile) echo "instance profile" ;;
    aws_budgets_budget) echo "budget alert" ;;
    *) echo "$1" ;;
  esac
}

# join_names: turn names on stdin, one a line, into "a, b and c", without repeats.
join_names() {
  sort -u | awk '{ names[NR] = $0 }
    END { for (i = 1; i <= NR; i++) printf "%s%s", names[i], (i == NR ? "" : (i == NR - 1 ? " and " : ", ")) }'
}

# describe_plan PLAN_JSON: say in plain words what applying the plan does, so no
# one types yes without knowing it deletes the server and its data. Reads
# $commit and $previous_instance where they're set.
describe_plan() {
  local hours short="${commit:-}" created="" updated="" deleted="" limit
  short="${short:0:7}"
  hours="$(jq -r '.variables.auto_terminate_hours.value // 8' <<<"$1")"
  if [ "$hours" = 0 ]; then
    limit="    It has no time limit, so it runs until make aws-destroy."
  else
    limit="    It terminates itself, with its data, $hours hours after it starts."
  fi
  while IFS='|' read -r type actions id old_cidr new_cidr; do
    case "$type:$actions" in
      aws_instance:create)
        if [ -n "${previous_instance:-}" ]; then
          echo "  - create a new server running $short, for about \$0.024 an hour, in place of"
          echo "    $previous_instance, which has terminated."
        else
          echo "  - create your server, running $short, for about \$0.024 an hour."
        fi
        echo "$limit"
        ;;
      aws_instance:delete+create | aws_instance:create+delete)
        echo "  - replace your server ($id) with a new one running $short."
        echo "    This deletes the old server and its data: Terraform shows \"Destroying...\""
        echo "    for a minute or two while it does. The new server costs about \$0.024 an hour."
        echo "$limit"
        ;;
      aws_instance:update) echo "  - update your server ($id) in place. It keeps its data." ;;
      aws_instance:delete) echo "  - delete your server ($id) and its data." ;;
      aws_vpc_security_group_ingress_rule:update | aws_vpc_security_group_ingress_rule:delete+create)
        echo "  - let requests from ${new_cidr%/32} reach the server, in place of ${old_cidr%/32}."
        ;;
      *:create) created+="$(friendly_name "$type")"$'\n' ;;
      *:update) updated+="$(friendly_name "$type")"$'\n' ;;
      *:delete) deleted+="$(friendly_name "$type")"$'\n' ;;
      *) echo "  - replace its $(friendly_name "$type") ($id)." ;;
    esac
  done < <(jq -r '.resource_changes[]
    | select(.mode == "managed" and .change.actions != ["no-op"])
    | [.type, (.change.actions | join("+")), (.change.before.id // ""), (.change.before.cidr_ipv4 // ""), (.change.after.cidr_ipv4 // "")]
    | join("|")' <<<"$1")
  # Everything here except the server is free; update this if that changes.
  [ -z "$created" ] || echo "  - create its $(printf %s "$created" | join_names), which are free."
  [ -z "$updated" ] || echo "  - update its $(printf %s "$updated" | join_names)."
  [ -z "$deleted" ] || echo "  - delete its $(printf %s "$deleted" | join_names)."
}

# plan_and_apply NOTHING_TO_DO TERRAFORM_PLAN_ARGUMENTS...: plan, say in plain
# words what applying the plan does, ask, and apply exactly that plan. Prints
# NOTHING_TO_DO instead when there's nothing to change.
plan_and_apply() {
  local nothing_to_do="$1" plan status=0 answer
  shift
  # A saved plan holds the variables' values, such as your email address and IP,
  # so it goes in a temporary folder that's deleted on exit, not in infra/.
  plan_dir="$(mktemp -d)"
  trap 'rm -rf "$plan_dir"' EXIT
  plan="$plan_dir/tfplan"
  echo "Working out what will change..."
  terraform -chdir=infra plan -input=false -detailed-exitcode -out="$plan" "$@" \
    >"$plan_dir/plan.log" 2>&1 || status=$?
  case "$status" in
    0)
      echo "$nothing_to_do"
      return
      ;;
    2) ;;
    *)
      cat "$plan_dir/plan.log" >&2
      fail "terraform plan failed"
      ;;
  esac
  echo
  echo "Typing yes will:"
  describe_plan "$(terraform -chdir=infra show -json "$plan")"
  while :; do
    read -rp "Type yes to go ahead, or plan to see Terraform's full plan: " answer
    case "$answer" in
      yes) break ;;
      plan) terraform -chdir=infra show "$plan" ;;
      *) fail "cancelled: nothing changed" ;;
    esac
  done
  terraform -chdir=infra apply -input=false "$plan"
}
