# shellcheck shell=bash
# Shared by the aws-* scripts, which source it after cd-ing to the repository
# root. Each script calls use_project first. on_server and require_running read
# $region and $instance, so set them first.
# shellcheck disable=SC2154  # region and instance come from the sourcing script

# Where the server keeps its deadline, as a Unix time (infra/bootstrap.sh.tftpl).
# shellcheck disable=SC2034  # read by the sourcing scripts
deadline_file=/var/lib/groundline/terminate-at

# The AWS CLI profile the scripts sign in with, unless AWS_PROFILE names
# another. make aws-up and make aws-project sign it in to the project you choose
# in the browser.
wizard_profile=groundline-deploy

# Terraform keeps each AWS project's state in its own workspace, named after the
# account ID, in this folder.
workspaces_dir=infra/terraform.tfstate.d

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

# On Ctrl-C, end the current line, so make's "Interrupt" message starts on a
# line of its own. Only the script you ran does this, not the scripts it runs,
# so there's one line break, not one from each.
if [ -z "${GROUNDLINE_AWS_SCRIPT:-}" ]; then
  export GROUNDLINE_AWS_SCRIPT=$$
  trap 'echo; exit 130' INT
fi

ok() { echo "${green}ok:${reset} $*"; }
warn() { echo "${yellow}warning:${reset_err} $*" >&2; }
error() { echo "${red}error:${reset_err} $*" >&2; }
fail() {
  error "$*"
  echo >&2
  exit 1
}

# top_level: true in the script you ran, false in one it runs, such as
# aws-deploy run by make aws-up.
top_level() { [ "${GROUNDLINE_AWS_SCRIPT:-}" = $$ ]; }

# answer_ok and answer_warn TEXT: a status about the answer just given. A blank
# line separates it from the answer, and it's indented like the question, so it
# reads as part of that question's block.
answer_ok() { printf '\n  %s\n' "${green}ok:${reset} $*"; }
answer_warn() { printf '\n  %s\n' "${yellow}warning:${reset_err} $*" >&2; }

# step TEXT: a heading for one part of a longer run, such as make aws-up's steps.
step() { printf '\n%s\n' "${bold}$*${reset}"; }

# The layout every make aws-* command follows, so each kind of line is easy to
# tell apart. Blocks are separated by a blank line.
# - A step heading (step), with its explanation directly below it.
# - An explanation: a sentence at the left margin, then bullets (bullet).
# - What the script is doing: an -ing phrase ending in "..." (doing), with the
#   results of that work indented under it (check_ok, check_error).
# - A question, indented (ask, confirm).
# - A status: indented under the question it answers (answer_ok, answer_warn),
#   or at the left margin when it stands alone (ok, warn).
bullet() { echo "  · $*"; }

# doing TEXT: say what the script is doing now, such as "Checking the server".
doing() { printf '\n%s...\n' "$*"; }

# check_ok, check_warn and check_error TEXT: a result of the work the doing
# line above describes, indented under it.
check_ok() { echo "  ${green}ok:${reset} $*"; }
check_warn() { echo "  ${yellow}warning:${reset_err} $*" >&2; }
check_error() { echo "  ${red}error:${reset_err} $*" >&2; }

# ask PROMPT [DEFAULT]: read one line into $answer, or DEFAULT if it's empty.
ask() {
  local suffix=""
  [ -n "${2:-}" ] && suffix=" [$2]"
  echo
  read -rp "  $1$suffix: " answer || fail "cancelled: no answer to \"$1\""
  answer="${answer:-${2:-}}"
}

# choose QUESTION COUNT [DEFAULT]: ask QUESTION, and read a number from 1 to
# COUNT into $answer. QUESTION ends with the options in short, such as "(1 main,
# 2 your branch)", so it makes sense without the list above it. With no
# DEFAULT, pressing Enter asks again, so a number has to be typed.
choose() {
  while :; do
    ask "$1" "${3:-}"
    [[ "$answer" =~ ^[0-9]+$ ]] && [ "$answer" -ge 1 ] && [ "$answer" -le "$2" ] && return
    echo "  Enter a number from 1 to $2."
  done
}

# confirm QUESTION: true if the answer is yes or empty. With no input left, such
# as when nobody is typing, it's false, so nothing is used or pushed unasked.
confirm() {
  local reply
  echo
  read -rp "  $1 [Y/n]: " reply || return 1
  case "$reply" in "" | [Yy] | [Yy][Ee][Ss]) return 0 ;; *) return 1 ;; esac
}

# use_project: pick the AWS project to act on and its Terraform state: the one
# AWS_PROFILE is signed in to, or else the wizard's profile. Sets $region,
# $account and $project_name, and exports TF_WORKSPACE, named after the
# account, so every terraform command uses that project's state.
use_project() {
  export AWS_PROFILE="${AWS_PROFILE:-$wizard_profile}"
  region="${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}"
  [ -n "$region" ] || fail "no AWS project chosen yet: run make aws-up or make aws-project"
  account="$(aws sts get-caller-identity --region "$region" --query Account --output text)" \
    || fail "the AWS CLI isn't signed in: run make aws-project to choose a project"
  export TF_WORKSPACE="$account"
  # Looked up once here, so the name doesn't change partway through a run if a
  # later lookup fails. The profile may not be allowed to read it.
  project_name="$(aws account get-account-information --region "$region" \
    --query AccountName --output text 2>/dev/null || true)"
  [ "$project_name" != None ] || project_name=""
}

# signed_in: true if the profile has a Region and a working sign-in.
signed_in() {
  local region
  region="${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}"
  [ -n "$region" ] && aws sts get-caller-identity --region "$region" >/dev/null 2>&1
}

# previous_account: the account ID of the project the profile last signed in
# to, from the login_session line aws login saves, or nothing.
previous_account() {
  aws configure get login_session 2>/dev/null \
    | sed -n 's/^arn:aws[a-z-]*:[a-z]*::\([0-9]\{12\}\):.*/\1/p' || true
}

# choose_project: have the developer sign in to the project in the console and
# copy its Region from there, then sign the profile in to that project's
# session in the browser.
choose_project() {
  local current
  export AWS_PROFILE="${AWS_PROFILE:-$wizard_profile}"
  current="$(aws configure get region 2>/dev/null || true)"
  echo
  echo "In your browser, sign in to the AWS project for your test deployment."
  bullet "Sign in at https://console.aws.amazon.com"
  bullet "No project yet? Create one at https://settings.aws.com > Project >"
  echo "    Create project, then sign in to it."
  echo
  read -rp "  Press Enter once you're signed in to the project in your browser: " _ || fail "cancelled: no project chosen"
  echo
  echo "Each project has its own Region. To copy it, in the console go to"
  echo "Manage projects > Overview > Additional Info > Region, and use its Copy button."
  while :; do
    ask "The project's Region code, such as us-east-2" "$current"
    [[ "$answer" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]+$ ]] && break
    echo "  That isn't a Region code. Codes look like us-east-2 or eu-west-1."
  done
  aws configure set region "$answer" --profile "$AWS_PROFILE"
  echo
  echo "Next, your browser opens to give these scripts access to the project."
  bullet "Choose the session of the project you just signed in to."
  echo
  read -rp "  Press Enter to open the browser and choose the project's session: " _ || fail "cancelled: no project chosen"
  aws_login
}

# aws_login: sign the profile in to the project whose session the developer
# chooses in the browser. aws login prints its sign-in link, the credentials it
# saved and a tip; only the link is shown, in case the browser doesn't open,
# and the rest only if it fails.
aws_login() {
  local log
  log="$(mktemp)"
  # PYTHONUNBUFFERED makes aws login print the link at once, not when it exits.
  if ! PYTHONUNBUFFERED=1 aws login --profile "$AWS_PROFILE" 2>&1 \
    | awk -v logfile="$log" '/^https:\/\// { print "  If the browser didn'"'"'t open, use this link:"; print "  " $0; fflush() } { print > logfile }'; then
    cat "$log" >&2
    rm -f "$log"
    fail "aws login failed"
  fi
  rm -f "$log"
}

# project_label: the project's name and account ID, such as "Groundline
# (123456789012)", or only the ID if use_project couldn't read the name.
project_label() {
  if [ -n "${project_name:-}" ]; then
    echo "$project_name ($account)"
  else
    echo "account $account"
  fi
}

# deployment_name: the deployment_name in infra/terraform.tfvars, or nothing.
# A missing file returns nothing too: GNU sed exits 2 on it, which pipefail
# would pass on and set -e would stop the script for, without a message.
deployment_name() {
  [ -f infra/terraform.tfvars ] || return 0
  sed -n 's/^[[:space:]]*deployment_name[[:space:]]*=[[:space:]]*"\(.*\)".*/\1/p' \
    infra/terraform.tfvars 2>/dev/null | tail -n 1
}

# anthropic_parameter NAME: the Parameter Store name for deployment NAME's
# Anthropic API key, as infra/main.tf builds it.
anthropic_parameter() { echo "/groundline/$1/anthropic-api-key"; }

# running_deployments: the deployment names of the groundline servers starting
# or running in this project, one a line, from their deployment tags.
running_deployments() {
  # shellcheck disable=SC2016  # the backticks are JMESPath's, not the shell's
  aws ec2 describe-instances --region "$region" \
    --filters Name=tag:app,Values=groundline Name=instance-state-name,Values=pending,running \
    --query 'Reservations[].Instances[].Tags[?Key==`deployment`].Value[]' --output text \
    | tr '\t' '\n' | sed '/^$/d' | sort -u
}

# other_deployments: say which other AWS projects still hold a deployment of
# yours, read from their Terraform state, and how to reach it.
other_deployments() {
  local state other
  for state in "$workspaces_dir"/*/terraform.tfstate; do
    [ -f "$state" ] || continue
    other="$(basename "$(dirname "$state")")"
    [ "$other" != "$account" ] || continue
    [ -n "$(jq -r '.outputs.instance_id.value // empty' "$state" 2>/dev/null)" ] || continue
    echo
    echo "You also have a test deployment in account $other."
    bullet "To check it or delete it, run make aws-project and choose that project,"
    echo "    then make aws-status or make aws-destroy."
  done
}

# The repository the server clones main from, and the default for every commit.
# shellcheck disable=SC2034  # read by the sourcing scripts
main_repository=https://github.com/groundline-dev/groundline.git

# https_url URL: a GitHub remote's URL as the server clones it, over HTTPS:
# git@github.com:alice/groundline becomes https://github.com/alice/groundline.git.
https_url() {
  local url="$1"
  case "$url" in
    git@github.com:*) url="https://github.com/${url#git@github.com:}" ;;
    ssh://git@github.com/*) url="https://github.com/${url#ssh://git@github.com/}" ;;
  esac
  [[ "$url" == *.git ]] || url="$url.git"
  echo "$url"
}

# remote_with COMMIT: the HTTPS URL of a git remote that has COMMIT, preferring
# groundline-dev/groundline. Fails if no remote has it, as far as the last
# git fetch knows.
remote_with() {
  local remotes remote url
  remotes="$(git for-each-ref --contains "$1" --format='%(refname)' refs/remotes | cut -d/ -f3 | sort -u)"
  [ -n "$remotes" ] || return 1
  for remote in $remotes; do
    url="$(https_url "$(git remote get-url "$remote")")"
    if [ "$url" = "$main_repository" ]; then
      echo "$url"
      return
    fi
  done
  https_url "$(git remote get-url "$(head -n 1 <<<"$remotes")")"
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

# what_next [DEADLINE]: what you can do with your running server. Each command
# that leaves the server running ends with it, so it's still in view after you
# run one of them, followed by a blank line before your shell prompt. A script
# that another one runs, such as aws-deploy run by make aws-up, leaves it out,
# so it's shown once, at the end. DEADLINE is the deadline file's contents, a
# time or "none", when the command has read it; reading it takes a call to the
# server, so the other commands leave it out.
what_next() {
  local app_url deadline="${1:-}" hours
  top_level || return 0
  app_url="$(terraform -chdir=infra output -raw app_url 2>/dev/null)" || return 0
  [ -n "$app_url" ] || return 0
  step "What next"
  echo "To try your server, at $app_url:"
  bullet "make aws-key creates an API key, for trying requests at $app_url/docs."
  bullet "make aws-smoke-live sends one real commission check, which costs under a cent."
  bullet "To call it from your app on this computer, use $app_url as the base URL"
  echo "    and an API key from make aws-key. See docs/deploying-to-aws.md, \"Calling it from your app\"."
  echo
  echo "Your server is still running and costing money."
  hours="$(terraform -chdir=infra output -raw auto_terminate_hours 2>/dev/null || echo 8)"
  case "$deadline" in
    "")
      bullet "make aws-status shows when it terminates itself."
      bullet "make aws-reset-deadline moves that to $hours hours from now."
      ;;
    none) bullet "It has no time limit, so it costs about \$17 a month." ;;
    *)
      bullet "It terminates itself, with its data, at $(describe_deadline "$deadline")."
      bullet "make aws-reset-deadline moves that to $hours hours from now."
      ;;
  esac
  bullet "make aws-destroy deletes it, with its data and your Anthropic API key. Run it"
  echo "    even after the server terminates itself, to remove what it leaves behind."
  echo
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
    limit="    It terminates itself, with its data, $hours hours after it starts.
    You can also delete it manually with make aws-destroy."
  fi
  while IFS='|' read -r type actions id old_cidr new_cidr; do
    case "$type:$actions" in
      aws_instance:create)
        if [ -n "${previous_instance:-}" ]; then
          echo "  · create a new server running $short, for about \$0.024 an hour, in place of"
          echo "    $previous_instance, which has terminated."
        else
          echo "  · create your server, running $short, for about \$0.024 an hour."
        fi
        echo "$limit"
        ;;
      aws_instance:delete+create | aws_instance:create+delete)
        echo "  · replace your server ($id) with a new one running $short."
        echo "    This deletes the old server and its data: Terraform shows \"Destroying...\""
        echo "    for a minute or two while it does. The new server costs about \$0.024 an hour."
        echo "$limit"
        ;;
      aws_instance:update) echo "  · update your server ($id) in place. It keeps its data." ;;
      aws_instance:delete) echo "  · delete your server ($id) and its data." ;;
      aws_vpc_security_group_ingress_rule:update | aws_vpc_security_group_ingress_rule:delete+create)
        echo "  · let requests from ${new_cidr%/32} reach the server, in place of ${old_cidr%/32}."
        ;;
      *:create) created+="$(friendly_name "$type")"$'\n' ;;
      *:update) updated+="$(friendly_name "$type")"$'\n' ;;
      *:delete) deleted+="$(friendly_name "$type")"$'\n' ;;
      *) echo "  · replace its $(friendly_name "$type") ($id)." ;;
    esac
  done < <(jq -r '.resource_changes[]
    | select(.mode == "managed" and .change.actions != ["no-op"])
    | [.type, (.change.actions | join("+")), (.change.before.id // ""), (.change.before.cidr_ipv4 // ""), (.change.after.cidr_ipv4 // "")]
    | join("|")' <<<"$1")
  # Everything here except the server is free; update this if that changes.
  [ -z "$created" ] || echo "  · create its $(printf %s "$created" | join_names), which are free."
  [ -z "$updated" ] || echo "  · update its $(printf %s "$updated" | join_names)."
  [ -z "$deleted" ] || echo "  · delete its $(printf %s "$deleted" | join_names)."
}

# show_plan PLAN: show Terraform's plan, keeping only the lines worth checking:
# which resources it creates, updates, replaces or deletes, each setting that
# changes and which change forces a replacement, and the totals. FULL_PLAN=1
# shows all of it, every setting of every resource.
show_plan() {
  if [ -n "${FULL_PLAN:-}" ]; then
    terraform -chdir=infra show "$1"
    return
  fi
  echo
  echo "Terraform's plan, short version (FULL_PLAN=1 shows all of it):"
  terraform -chdir=infra show -no-color "$1" \
    | grep -E '^  # |forces replacement|^[[:space:]]+~ |^Plan: ' || true
}

# plan_and_apply NOTHING_TO_DO TERRAFORM_PLAN_ARGUMENTS...: plan, say in plain
# words what applying the plan does, ask, and apply exactly that plan. Prints
# NOTHING_TO_DO instead when there's nothing to change. Sets $plan_changed to
# yes when it changed something in AWS.
plan_and_apply() {
  local nothing_to_do="$1" plan status=0 summary
  plan_changed=""
  shift
  # A saved plan holds the variables' values, such as your email address and IP,
  # so it goes in a temporary folder that's deleted on exit, not in infra/.
  plan_dir="$(mktemp -d)"
  trap 'rm -rf "$plan_dir"' EXIT
  plan="$plan_dir/tfplan"
  doing "Working out what will change"
  terraform -chdir=infra plan -input=false -detailed-exitcode -out="$plan" "$@" \
    >"$plan_dir/plan.log" 2>&1 || status=$?
  case "$status" in
    0)
      echo
      ok "$nothing_to_do"
      return
      ;;
    2) ;;
    *)
      cat "$plan_dir/plan.log" >&2
      fail "terraform plan failed"
      ;;
  esac
  summary="$(describe_plan "$(terraform -chdir=infra show -json "$plan")")"
  # A plan that changes only outputs, such as one adding a new output, changes
  # nothing in AWS, so it's applied without asking.
  if [ -z "$summary" ]; then
    terraform -chdir=infra apply -input=false "$plan" >/dev/null
    echo
    ok "$nothing_to_do"
    return
  fi
  echo
  echo "Making these changes will:"
  echo "$summary"
  # No default: applying can delete the server and its data, so Enter alone
  # doesn't approve it.
  echo
  echo "  1) Make these changes"
  echo "  2) Show Terraform's plan first"
  echo "  3) Cancel, changing nothing"
  choose "Make the changes above? (1 make them, 2 show the plan, 3 cancel)" 3
  if [ "$answer" = 2 ]; then
    show_plan "$plan"
    echo
    echo "  1) Make these changes"
    echo "  2) Cancel, changing nothing"
    choose "Make the changes above? (1 make them, 2 cancel)" 2
    [ "$answer" = 1 ] || answer=3
  fi
  [ "$answer" = 1 ] || fail "cancelled: nothing changed"
  plan_changed=yes
  doing "Making the changes"
  terraform -chdir=infra apply -input=false "$plan"
}
