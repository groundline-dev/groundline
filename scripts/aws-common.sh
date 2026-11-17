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
# The helpers can't enforce these rules, so follow them by hand:
# - Each question makes sense without the explanation above it, and says what
#   its answer leads to.
# - A question whose answer can delete data has no default (choose with no
#   DEFAULT), so a number has to be typed.
# - Each command that leaves the server running ends with what_next.
# - Every command, errors included, ends with a blank line before the shell
#   prompt (fail and what_next print it).
# - Each value is looked up once and reused, so a later lookup that fails
#   can't change it partway through a run.
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
  read -rp "  $1$suffix: " answer || fail "canceled: no answer to \"$1\""
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
  # The AWS CLI's own error for an expired sign-in says to run aws login, which
  # signs in a different profile, so only its other errors are shown.
  if ! account="$(aws sts get-caller-identity --region "$region" --query Account \
    --output text 2>&1)"; then
    case "$account" in
      *[Ee]xpired*) fail "your AWS sign-in has expired. Run make aws-project to sign in again." ;;
      *) fail "couldn't check your AWS sign-in: $account" ;;
    esac
  fi
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
  read -rp "  Press Enter once you're signed in to the project in your browser: " _ || fail "canceled: no project chosen"
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
  read -rp "  Press Enter to open the browser and choose the project's session: " _ || fail "canceled: no project chosen"
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

# tfvars_value KEY: KEY's value in infra/terraform.tfvars, without its quotes,
# or nothing. The last line setting KEY wins, as in Terraform. A missing file
# returns nothing too, so the scripts can call it before make aws-up writes it.
tfvars_value() {
  [ -f infra/terraform.tfvars ] || return 0
  awk -v key="$1" '
    $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
      value = $0
      sub(/^[^=]*=[[:space:]]*/, "", value)
      if (value ~ /^"/) { sub(/^"/, "", value); sub(/".*/, "", value) }
      else sub(/[[:space:]#].*/, "", value)
      found = value
    }
    END { if (found != "") print found }
  ' infra/terraform.tfvars 2>/dev/null
}

# save_setting KEY VALUE: set KEY = "VALUE" in infra/terraform.tfvars, in place
# of the first line that sets it (keeping that line's comment), or at the end.
# Later lines setting KEY are dropped, so the file says one thing.
save_setting() {
  local key="$1" value="$2" temporary
  temporary="$(mktemp infra/terraform.tfvars.XXXXXX)"
  if ! awk -v key="$key" -v value="$value" '
    $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
      comment=""
      if (match($0, /"[[:space:]]*#/)) comment=substr($0, RSTART + 1)
      if (!written) print key " = \"" value "\"" comment
      written=1; next
    }
    { print }
    END { if (!written) print key " = \"" value "\"" }
  ' infra/terraform.tfvars >"$temporary" || ! mv "$temporary" infra/terraform.tfvars; then
    rm -f "$temporary"
    fail "could not save $key in infra/terraform.tfvars"
  fi
}

# deployment_name: the deployment_name in infra/terraform.tfvars, or nothing.
deployment_name() { tfvars_value deployment_name; }

# Read one supported literal mode. Status 1 means absent; 2 means invalid.
# This reader never changes settings or prints an interactive prompt.
read_access_mode() {
  local setting
  setting="$(sed -n '/^[[:space:]]*access_mode[[:space:]]*=/p' infra/terraform.tfvars)"
  [ -n "$setting" ] || return 1
  if [[ "$setting" =~ ^[[:space:]]*access_mode[[:space:]]*=[[:space:]]*\"(restricted|unrestricted)\"[[:space:]]*(#.*)?$ ]]; then
    echo "${BASH_REMATCH[1]}"
  else
    return 2
  fi
}

save_access_mode() {
  case "$1" in restricted | unrestricted) ;; *) fail "unsupported access mode: $1" ;; esac
  save_setting access_mode "$1"
}

# configured_access_mode [fail|ask]: print only the supported mode on stdout.
# Missing settings default to restricted. Only the wizard asks about invalid
# settings; prompts and notices go to stderr so callers can capture the mode.
configured_access_mode() {
  local on_invalid="${1:-fail}" mode status=0 notice=warn access_mode
  case "$on_invalid" in
    fail) ;;
    ask) notice=answer_warn ;;
    *) fail "unsupported invalid access-mode action: $on_invalid" ;;
  esac
  mode="$(read_access_mode)" || status=$?
  case "$status" in
    0) echo "$mode" ;;
    1)
      save_access_mode restricted
      "$notice" 'No access_mode set. Saved the IP-restricted HTTPS default.'
      echo restricted
      ;;
    *)
      [ "$on_invalid" = ask ] || fail 'Unrecognized access_mode. Run make aws-up to choose restricted or unrestricted, or correct infra/terraform.tfvars.'
      answer_warn "the saved access_mode is unrecognized; choose a supported HTTPS access mode"
      choose_access_mode >&2
      save_access_mode "$access_mode"
      answer_ok "saved $access_mode HTTPS access" >&2
      echo "$access_mode"
      ;;
  esac
}

# choose_access_mode [DEFAULT]: ask who may reach the app and set
# $access_mode. DEFAULT is 1 (IP-restricted, for testing) or 2 (unrestricted,
# which a lasting deployment's callers need).
choose_access_mode() {
  local default="${1:-1}"
  if [ "$default" = 1 ]; then
    echo "  1) IP-restricted HTTPS for testing from this computer (recommended)"
    echo "  2) Unrestricted HTTPS for callers from any IP"
  else
    echo "  1) IP-restricted HTTPS for testing from this computer"
    echo "  2) Unrestricted HTTPS for callers from any IP (recommended)"
  fi
  bullet "Both use automatic certificates; protected API endpoints require API keys."
  choose "Who may access the HTTPS application? (1 IP-restricted, 2 unrestricted)" 2 "$default"
  access_mode=restricted
  [ "$answer" = 1 ] || access_mode=unrestricted
}

# choose_lifetime: ask whether the deployment is disposable or lasting, and set
# $lifetime. It's asked once per folder: a folder that manages a lasting
# deployment manages only that one.
choose_lifetime() {
  echo "  1) Disposable: for trying a commit. It terminates itself after 8 hours, and"
  echo "     make aws-destroy deletes it with its data."
  echo "  2) Lasting: for staging or production. It keeps running, and keeps its data,"
  echo "     API keys, settings and address when you upgrade it, until you delete it."
  choose "Disposable or lasting? (1 disposable, 2 lasting)" 2 1
  lifetime=disposable
  [ "$answer" = 1 ] || lifetime=lasting
}

# configured_lifetime: the lifetime in infra/terraform.tfvars, or disposable,
# as Terraform's default is.
configured_lifetime() {
  local lifetime
  lifetime="$(tfvars_value lifetime)"
  case "${lifetime:-disposable}" in
    disposable | lasting) echo "${lifetime:-disposable}" ;;
    *) fail "Unrecognized lifetime: $lifetime. Set lifetime = \"disposable\" or \"lasting\" in infra/terraform.tfvars." ;;
  esac
}

public_ipv4() {
  local ip octet
  local octets
  ip="$(curl --disable --fail --silent --show-error --max-time 10 https://checkip.amazonaws.com | tr -d '[:space:]')" || ip=""
  [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || fail "couldn't find this computer's public IPv4 address"
  IFS=. read -r -a octets <<<"$ip"
  for octet in "${octets[@]}"; do
    ((10#$octet <= 255)) || fail "invalid public IPv4 address"
  done
  echo "$ip"
}

# Outputs describe the actual deployed access, independently of local defaults.
deployed_access_mode() {
  local mode
  mode="$(output access_mode)" || return 1
  case "$mode" in
    restricted | unrestricted) echo "$mode" ;;
    *) fail "unsupported deployed access mode: $mode; deploy a supported HTTPS revision" ;;
  esac
}

# anthropic_parameter NAME: the Parameter Store name for deployment NAME's
# Anthropic API key, as infra/main.tf builds it.
anthropic_parameter() { echo "/groundline/$1/anthropic-api-key"; }

# running_lifetimes: the groundline servers starting or running in this
# project, one "NAME LIFETIME" a line, from their tags. A server from before
# lifetimes counts as disposable.
running_lifetimes() {
  aws ec2 describe-instances --region "$region" \
    --filters Name=tag:app,Values=groundline Name=instance-state-name,Values=pending,running \
    --output json \
    | jq -r '.Reservations[].Instances[] | (.Tags // [] | from_entries) as $tags
      | select($tags.deployment) | "\($tags.deployment) \($tags["groundline-lifetime"] // "disposable")"' \
    | sort -u
}

# running_deployments: the deployment names of the groundline servers starting
# or running in this project, one a line, from their deployment tags.
running_deployments() {
  running_lifetimes | cut -d' ' -f1 | sort -u
}

# shared_project_warning NAME LIFETIME: warn when deployment NAME shares this
# project with deployments of the other kind. Budget alerts cover the whole
# project, so a lasting deployment's cost sets off the $5 disposable alerts,
# and theirs counts toward its budget. It warns, since some have only one
# project.
shared_project_warning() {
  local name="$1" lifetime="$2" running others lasting
  running="$(running_lifetimes 2>/dev/null)" || return 0
  others="$(awk -v name="$name" '$1 != name { print $1 }' <<<"$running" | join_names)"
  lasting="$(awk -v name="$name" '$1 != name && $2 == "lasting" { print $1 }' <<<"$running" | join_names)"
  if [ "$lifetime" = lasting ] && [ -n "$others" ]; then
    answer_warn "other deployments run in $(project_label): $others. Budget alerts cover the whole project, so this deployment's cost sets off their \$5 alerts, and theirs counts toward its budget. A lasting deployment is best in an AWS project of its own."
  elif [ "$lifetime" = disposable ] && [ -n "$lasting" ]; then
    answer_warn "the lasting deployment $lasting runs in $(project_label). Budget alerts cover the whole project, so its cost sets off this deployment's \$5 alert. Test deployments are best in another AWS project."
  fi
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

# folder_lasting_deployments: the deployments this folder's infra/data/ state
# holds, in any AWS project, one "ACCOUNT NAME LIFETIME" a line. The data
# volume outlives the server, so this still lists a lasting deployment whose
# server make aws-destroy deleted.
folder_lasting_deployments() {
  local state
  for state in infra/data/terraform.tfstate.d/*/terraform.tfstate; do
    [ -f "$state" ] || continue
    jq -r --arg account "$(basename "$(dirname "$state")")" \
      'select(.outputs.volume_id.value) | "\($account) \(.outputs.deployment_name.value // "") \(.outputs.lifetime.value // "disposable")"' \
      "$state" 2>/dev/null || true
  done
}

# guard_lasting_folder: fail unless this run deploys what the folder already
# holds. infra/terraform.tfvars is the same for every AWS project the folder
# deploys to, and a lasting deployment needs different settings from a
# disposable one, so a folder that holds a lasting deployment deploys nothing
# else. A disposable deployment can't become lasting either: a lasting one
# starts with its own data and API keys.
guard_lasting_folder() {
  local name lifetime held_account held_name held_lifetime
  name="$(deployment_name)"
  lifetime="$(configured_lifetime)"
  while read -r held_account held_name held_lifetime; do
    [ -n "$held_account" ] || continue
    if [ "$held_lifetime" = lasting ]; then
      if [ "$held_account" != "$account" ] || [ "$held_name" != "$name" ] || [ "$lifetime" != lasting ]; then
        fail "this folder manages the lasting deployment $held_name in account $held_account, so it deploys nothing else. Use another clone or worktree for other deployments, such as: git worktree add ../groundline-test"
      fi
    elif [ "$lifetime" = lasting ] && [ "$held_account" = "$account" ] && [ "$held_name" = "$name" ]; then
      fail "$name is a disposable deployment, and one can't become lasting: a lasting deployment starts with its own data and API keys. Run make aws-destroy first, or choose another name."
    fi
  done < <(folder_lasting_deployments)
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

# The reminder for a server that terminated itself: terminating removes the
# instance and its disks, and the server releases its Elastic IP first, so the
# rest is still there.
gone_reminder() {
  echo "Terminating removed the server, its data and its address. Run make aws-destroy to" >&2
  echo "remove the security group, IAM role, instance profile and budget it left behind," >&2
  echo "or make aws-deploy for a new server." >&2
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
      echo >&2
      exit 1
      ;;
    *) fail "the server ($instance) is $state, not running: run make aws-deploy to replace it" ;;
  esac
}

# on_server COMMAND: run COMMAND on the server as root with SSM Run Command and
# print its output. If it fails, also print its error output, and fail.
on_server() {
  # Restart checks need short, bounded probes while SSM may be offline.
  if [ -n "${2:-}" ]; then
    on_server_bounded "$1" "$2"
    return
  fi
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

# on_server_bounded COMMAND SECONDS: no unbounded AWS waiter during recovery.
# Each AWS request is bounded too; delivery and remote execution have limits.
on_server_bounded() {
  local command="$1" seconds="$2" id response status remaining
  local until=$((SECONDS + seconds))
  local parameters
  parameters="$(jq -cn --arg command "$command" --arg timeout "$seconds" \
    '{commands:[$command],executionTimeout:[$timeout]}')"
  id="$(aws ssm send-command --region "$region" --instance-ids "$instance" \
    --cli-connect-timeout 3 --cli-read-timeout 5 --timeout-seconds 30 \
    --document-name AWS-RunShellScript --parameters "$parameters" \
    --query Command.CommandId --output text)" || return 1
  while [ "$SECONDS" -lt "$until" ]; do
    remaining=$((until - SECONDS))
    [ "$remaining" -le 5 ] || remaining=5
    if response="$(aws ssm get-command-invocation --region "$region" --command-id "$id" \
      --instance-id "$instance" --cli-connect-timeout 3 --cli-read-timeout "$remaining" \
      --output json 2>&1)"; then
      status="$(jq -r .Status <<<"$response")" || return 1
      case "$status" in
        Success) jq -r '.StandardOutputContent' <<<"$response"; return ;;
        Pending | InProgress | Delayed) ;;
        *) jq -r '.Status, .StandardErrorContent' <<<"$response" >&2; return 1 ;;
      esac
    else
      case "$response" in
        *InvocationDoesNotExist*) ;;
        *) echo "$response" >&2; return 1 ;;
      esac
    fi
    sleep 2
  done
  echo "SSM command $id did not complete within $seconds seconds" >&2
  return 1
}

# what_next [DEADLINE]: what you can do with your running server. Each command
# that leaves the server running ends with it, so it's still in view after you
# run one of them, followed by a blank line before your shell prompt. A script
# that another one runs, such as aws-deploy run by make aws-up, leaves it out,
# so it's shown once, at the end. DEADLINE is the deadline file's contents, a
# time or "none", when the command has read it; reading it takes a call to the
# server, so the other commands leave it out.
what_next() {
  local app_url deadline="${1:-}" hours mode
  top_level || return 0
  app_url="$(terraform -chdir=infra output -raw app_url 2>/dev/null)" || return 0
  [ -n "$app_url" ] || return 0
  step "What next"
  echo "To try your server, at $app_url:"
  bullet "make aws-key creates an API key, for trying requests at $app_url/docs."
  bullet "make aws-smoke-live sends one real commission check, which costs under a cent."
  mode="$(terraform -chdir=infra output -raw access_mode 2>/dev/null || true)"
  case "$mode" in
    restricted) bullet "IP-restricted HTTPS: only the configured tester IP can access application routes, including health and docs." ;;
    unrestricted) bullet "Unrestricted HTTPS: any caller IP can reach health/docs; protected endpoints require an API key." ;;
  esac
  bullet "To call it from your app's backend, use $app_url as the base URL"
  echo "    and an API key from make aws-key. See docs/deploying-to-aws.md, \"Calling it from your app\"."
  echo
  echo "Your server is still running and costing money."
  if [ "$(terraform -chdir=infra output -raw lifetime 2>/dev/null || true)" = lasting ]; then
    bullet "It's a lasting deployment, so it has no time limit and costs about \$18 a month."
    bullet "make aws-up upgrades it to another commit or settings. It keeps its data and address."
    bullet "make aws-destroy deletes the server and keeps its data and address, for about"
    echo "    \$4.25 a month; make aws-destroy DATA=delete deletes those too."
    echo
    return
  fi
  hours="$(terraform -chdir=infra output -raw auto_terminate_hours 2>/dev/null || echo 8)"
  case "$deadline" in
    "")
      bullet "make aws-status shows when it terminates itself."
      bullet "make aws-reset-deadline moves that to $hours hours from now."
      ;;
    none) bullet "It has no time limit, so it costs about \$18 a month." ;;
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

# describe_commit PLAN_JSON: the commit an update of the server moves it to,
# from its groundline-git-ref tag, and whether that's older than the one it
# runs, which is a rollback.
describe_commit() {
  local from to
  from="$(jq -r '[.resource_changes[] | select(.type == "aws_instance" and .change.actions == ["update"])
    | .change.before.tags["groundline-git-ref"] // empty][0] // empty' <<<"$1")"
  to="$(jq -r '[.resource_changes[] | select(.type == "aws_instance" and .change.actions == ["update"])
    | .change.after.tags["groundline-git-ref"] // empty][0] // empty' <<<"$1")"
  [ -n "$from" ] && [ -n "$to" ] && [ "$from" != "$to" ] || return 0
  echo "    It's upgraded from ${from:0:7} to ${to:0:7}."
  if git merge-base --is-ancestor "$to" "$from" 2>/dev/null; then
    echo "    ${to:0:7} is older than ${from:0:7}, so this rolls it back. If a database"
    echo "    migration ran since, restore the pre-upgrade snapshot first (docs/deploying-to-aws.md)."
  fi
}

# describe_settings PLAN_JSON: the app settings an update of the server
# changes, one a line, from its groundline-setting-<NAME> tags.
describe_settings() {
  jq -r '.resource_changes[]
    | select(.type == "aws_instance" and .change.actions == ["update"])
    | (.change.before.tags // {}) as $before | (.change.after.tags // {}) as $after
    | ([$before, $after] | map(keys) | add | unique)[]
    | select(startswith("groundline-setting-")) as $tag
    | select($before[$tag] != $after[$tag])
    | ($tag | ltrimstr("groundline-setting-")) as $name
    | if $after[$tag] == null then "    Its \($name) goes back to the default (it was \($before[$tag]))."
      elif $before[$tag] == null then "    Its \($name) becomes \($after[$tag]) (it was the default)."
      else "    Its \($name) becomes \($after[$tag]) (it was \($before[$tag]))." end' <<<"$1"
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
    aws_volume_attachment) echo "data volume attachment" ;;
    aws_eip_association) echo "address association" ;;
    *) echo "$1" ;;
  esac
}

# join_names: turn names on stdin, one a line, into "a, b and c", without repeats.
join_names() {
  sort -u | awk '{ names[NR] = $0 }
    END { for (i = 1; i <= NR; i++) printf "%s%s", names[i], (i == NR ? "" : (i == NR - 1 ? " and " : ", ")) }'
}

# describe_plan PLAN_JSON: say in plain words what applying the plan does, so no
# one types yes without knowing it deletes the server or its data. Reads
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
  while IFS='|' read -r type actions id old_cidr new_cidr old_port new_port size address; do
    case "$type:$actions" in
      aws_ebs_volume:create)
        echo "  · create its data volume ($size GiB, about \$$(awk -v gib="$size" 'BEGIN { printf "%.2f", gib * 0.08 }') a month), which keeps"
        echo "    the database, its API keys and the HTTPS certificate when the server is replaced."
        ;;
      aws_eip:create)
        echo "  · reserve its address, an Elastic IP, which stays the same when the server is replaced."
        echo "    It costs about \$3.60 a month, in place of the server's own address."
        ;;
      aws_ebs_volume:delete | aws_ebs_volume:delete+create | aws_ebs_volume:create+delete)
        echo "  · delete its data volume ($id): the database, its API keys and the HTTPS certificate."
        ;;
      aws_eip:delete | aws_eip:delete+create | aws_eip:create+delete)
        echo "  · release its address${address:+, $address}. Callers would need a new one."
        ;;
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
        echo "  · replace your server ($id) with a new one running $short. Its data volume and"
        echo "    address move to the new server, so it keeps its database, API keys and address."
        echo "    Terraform shows \"Destroying...\" for a few minutes while the old one stops."
        echo "    The new server costs about \$0.024 an hour."
        echo "$limit"
        ;;
      aws_instance:update)
        echo "  · update your server ($id) in place. It keeps its data."
        describe_commit "$1"
        describe_settings "$1"
        ;;
      aws_instance:delete) echo "  · delete your server ($id)." ;;
      aws_vpc_security_group_ingress_rule:update | aws_vpc_security_group_ingress_rule:delete+create)
        echo "  · let requests from ${new_cidr%/32} reach the server on TCP $new_port, in place of ${old_cidr%/32} on TCP $old_port."
        ;;
      *:create) created+="$(friendly_name "$type")"$'\n' ;;
      *:update) updated+="$(friendly_name "$type")"$'\n' ;;
      *:delete) deleted+="$(friendly_name "$type")"$'\n' ;;
      *) echo "  · replace its $(friendly_name "$type") ($id)." ;;
    esac
  done < <(jq -r '.resource_changes[]
    | select(.mode == "managed" and .change.actions != ["no-op"])
    | [.type, (.change.actions | join("+")), (.change.before.id // ""), (.change.before.cidr_ipv4 // ""), (.change.after.cidr_ipv4 // ""), (.change.before.from_port // ""), (.change.after.from_port // ""), ((.change.after.size // .change.before.size // "") | tostring), (.change.before.public_ip // "")]
    | join("|")' <<<"$1")
  # Everything here except the server, the data volume and the address is free;
  # update this if that changes.
  [ -z "$created" ] || echo "  · create its $(printf %s "$created" | join_names), which are free."
  [ -z "$updated" ] || echo "  · update its $(printf %s "$updated" | join_names)."
  [ -z "$deleted" ] || echo "  · delete its $(printf %s "$deleted" | join_names)."
}

# show_plan PLAN: show Terraform's plan, keeping only the lines worth checking:
# which resources it creates, updates, replaces or deletes, each setting that
# changes and which change forces a replacement, and the totals. FULL_PLAN=1
# shows all of it, every setting of every resource. ROOT is the Terraform folder
# the plan is for, such as infra.
show_plan() {
  local root="$1" plan="$2"
  if [ -n "${FULL_PLAN:-}" ]; then
    terraform -chdir="$root" show "$plan"
    return
  fi
  echo
  echo "Terraform's plan, short version (FULL_PLAN=1 shows all of it):"
  terraform -chdir="$root" show -no-color "$plan" \
    | grep -E '^  # |forces replacement|^[[:space:]]+~ |^Plan: ' || true
}

# make_plan ROOT TERRAFORM_PLAN_ARGUMENTS...: plan the Terraform folder ROOT,
# such as infra, and set $planned to the saved plan and $planned_summary to
# what applying it does, in plain words. $planned is empty when there's nothing
# to change. A plan that changes only outputs, such as one adding a new output,
# changes nothing in AWS, so it's applied at once, without asking.
plan_dirs=()
make_plan() {
  local root="$1" plan plan_dir plan_json status=0
  shift
  planned="" planned_summary=""
  # A saved plan holds the variables' values, such as your email address and IP,
  # so it goes in a temporary folder that's deleted on exit, not in infra/. Each
  # plan's folder is kept in plan_dirs, so a second plan doesn't leave the
  # first one's behind.
  plan_dir="$(mktemp -d)"
  plan_dirs+=("$plan_dir")
  trap 'rm -rf "${plan_dirs[@]}"' EXIT
  plan="$plan_dir/tfplan"
  terraform -chdir="$root" plan -input=false -detailed-exitcode -out="$plan" "$@" \
    >"$plan_dir/plan.log" 2>&1 || status=$?
  case "$status" in
    0) return ;;
    2) ;;
    *)
      cat "$plan_dir/plan.log" >&2
      fail "terraform plan failed in $root"
      ;;
  esac
  plan_json="$(terraform -chdir="$root" show -json "$plan")"
  # The data volume and the address are deleted only when that's what the run
  # is for: a disposable deployment's make aws-destroy, or DATA=delete. Any other
  # plan that would delete or replace them, such as after a hand edit to
  # infra/data/, stops here.
  if [ "${allow_data_deletion:-}" != yes ] && jq -e '[.resource_changes[]?
    | select(.mode == "managed" and (.type == "aws_ebs_volume" or .type == "aws_eip")
      and (.change.actions | index("delete")))] | length > 0' <<<"$plan_json" >/dev/null; then
    fail "this plan would delete the deployment's data volume or address, so nothing was changed. FULL_PLAN=1 shows Terraform's whole plan."
  fi
  planned_summary="$(describe_plan "$plan_json")"
  if [ -z "$planned_summary" ]; then
    terraform -chdir="$root" apply -input=false "$plan" >/dev/null
    return
  fi
  planned="$plan"
}

# apply_plans NOTHING_TO_DO SUMMARY ROOT PLAN [ROOT PLAN]...: say what applying
# the plans does (SUMMARY, from make_plan), ask once, and apply exactly those
# plans, in order. An empty PLAN is skipped. Prints NOTHING_TO_DO instead when
# every PLAN is empty. Sets $plan_changed to yes when it changed something in
# AWS.
apply_plans() {
  local nothing_to_do="$1" summary="$2" index
  shift 2
  local pairs=("$@")
  plan_changed=""
  if [ -z "$summary" ]; then
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
    for ((index = 0; index + 1 < ${#pairs[@]}; index += 2)); do
      [ -z "${pairs[index + 1]}" ] || show_plan "${pairs[index]}" "${pairs[index + 1]}"
    done
    echo
    echo "  1) Make these changes"
    echo "  2) Cancel, changing nothing"
    choose "Make the changes above? (1 make them, 2 cancel)" 2
    [ "$answer" = 1 ] || answer=3
  fi
  [ "$answer" = 1 ] || fail "canceled: nothing changed"
  plan_changed=yes
  doing "Making the changes"
  for ((index = 0; index + 1 < ${#pairs[@]}; index += 2)); do
    [ -z "${pairs[index + 1]}" ] || terraform -chdir="${pairs[index]}" apply -input=false "${pairs[index + 1]}"
  done
}

# plan_and_apply ROOT NOTHING_TO_DO TERRAFORM_PLAN_ARGUMENTS...: make_plan and
# apply_plans for one Terraform folder.
plan_and_apply() {
  local root="$1" nothing_to_do="$2"
  shift 2
  doing "Working out what will change"
  make_plan "$root" "$@"
  apply_plans "$nothing_to_do" "$planned_summary" "$root" "$planned"
}

# output NAME [ROOT]: read a required output of the Terraform folder ROOT
# (default infra); Terraform can succeed with an empty value.
output() {
  local value
  value="$(terraform -chdir="${2:-infra}" output -raw "$1")" && [ -n "$value" ] \
    || fail "no $1 in the Terraform state for $(project_label): run make aws-up first"
  printf '%s' "$value"
}

# Format successful checker output without changing machine-readable Python output.
check_results() {
  local result
  while IFS= read -r result; do check_ok "$result"; done
}
