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

# Colour and bold only where the output goes to a terminal, not to a file or a
# log, and not when NO_COLOR is set (https://no-color.org) or the terminal can't
# show it (TERM=dumb).
plain_terminal() { [ -n "${NO_COLOR:-}" ] || [ "${TERM:-}" = dumb ]; }
if [ -t 1 ] && ! plain_terminal; then
  bold=$'\033[1m' green=$'\033[32m' reset=$'\033[0m'
else
  bold="" green="" reset=""
fi
if [ -t 2 ] && ! plain_terminal; then
  red=$'\033[31m' yellow=$'\033[33m' reset_err=$'\033[0m'
else
  red="" yellow="" reset_err=""
fi

# The bullet: a middle dot where the terminal shows UTF-8, else a hyphen.
case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
  *[Uu][Tt][Ff]-8* | *[Uu][Tt][Ff]8*) dot='·' ;;
  *) dot='-' ;;
esac

# output_width FD: the columns to wrap at: the terminal's width (COLUMNS, if it's
# set, as most tools honour it), but never more than 78, which keeps lines easy
# to read; 78 when FD isn't a terminal. Worked out once, when this file is
# sourced, so every line of a run wraps the same way.
output_width() {
  local width="${COLUMNS:-}"
  if [ -z "$width" ] && [ -t "$1" ]; then
    width="$(tput cols 2>/dev/null || true)"
  fi
  [[ "$width" =~ ^[0-9]+$ ]] && [ "$width" -gt 0 ] || width=78
  [ "$width" -le 78 ] || width=78
  echo "$width"
}
out_width="$(output_width 1)"
err_width="$(output_width 2)"

# wrap_text WIDTH HANG TEXT: split TEXT into lines of at most WIDTH - HANG
# columns, into the array $wrapped, breaking only at spaces. A word never
# breaks, so a path or URL longer than a line has a line of its own and runs
# past the edge rather than being cut where copying it would miss a piece. A
# newline in TEXT starts a new line. Spaces between words become one.
wrap_text() {
  local avail=$(($1 - $2)) text="$3" paragraph word line
  local -a paragraphs words
  [ "$avail" -ge 20 ] || avail=20
  wrapped=()
  local IFS=$'\n'
  set -f
  # shellcheck disable=SC2206  # split on newlines; globbing is off
  paragraphs=($text)
  IFS=' '
  # ${a[@]+"${a[@]}"}: an empty array expands to nothing under set -u, which
  # bash 3.2 otherwise calls unbound.
  for paragraph in ${paragraphs[@]+"${paragraphs[@]}"}; do
    # shellcheck disable=SC2206  # split on spaces; globbing is off
    words=($paragraph)
    line=""
    for word in ${words[@]+"${words[@]}"}; do
      if [ -z "$line" ]; then
        line="$word"
      elif [ $((${#line} + 1 + ${#word})) -le "$avail" ]; then
        line+=" $word"
      else
        wrapped+=("$line")
        line="$word"
      fi
    done
    wrapped+=("$line")
  done
  set +f
  [ "${#wrapped[@]}" -gt 0 ] || wrapped=("")
}

# say WIDTH HANG FIRST TEXT [STYLE]: print TEXT wrapped at WIDTH columns. The
# first line starts with FIRST, such as "  ok: ", which is HANG columns wide
# without its colour codes; the lines after it are indented HANG spaces, so
# they line up under the text. STYLE, such as $bold, applies to the text of
# each line, after wrapping, so colour codes never count towards the width.
say() {
  local width="$1" hang="$2" first="$3" text="$4" style="${5:-}" end="" pad index
  [ -z "$style" ] || end="$reset"
  printf -v pad '%*s' "$hang" ''
  wrap_text "$width" "$hang" "$text"
  printf '%s\n' "$first$style${wrapped[0]}$end"
  for ((index = 1; index < ${#wrapped[@]}; index++)); do
    printf '%s\n' "$pad$style${wrapped[index]}$end"
  done
}

# On Ctrl-C, end the current line, so make's "Interrupt" message starts on a
# line of its own. Only the script you ran does this, not the scripts it runs,
# so there's one line break, not one from each.
if [ -z "${GROUNDLINE_AWS_SCRIPT:-}" ]; then
  export GROUNDLINE_AWS_SCRIPT=$$
  # The command you ran, such as make aws-up, for messages that say to run it
  # again, whichever script prints them.
  export GROUNDLINE_AWS_COMMAND="make $(basename "$0")"
  trap 'echo; exit 130' INT
fi

ok() { say "$out_width" 4 "${green}ok:${reset} " "$*"; }
warn() { say "$err_width" 9 "${yellow}warning:${reset_err} " "$*" >&2; }
error() { say "$err_width" 7 "${red}error:${reset_err} " "$*" >&2; }
fail() {
  # A message starting "canceled" is a cancel, wherever it ends the run.
  case "$*" in canceled*) record '.outcome = "canceled"' ;; esac
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
answer_ok() {
  echo
  say "$out_width" 6 "  ${green}ok:${reset} " "$*"
}
answer_warn() {
  echo >&2
  say "$err_width" 11 "  ${yellow}warning:${reset_err} " "$*" >&2
}

# step TEXT: a heading for one part of a longer run, such as make aws-up's steps.
step() {
  echo
  say "$out_width" 0 "" "$*" "$bold"
}

# explain TEXT: an explanation, as a sentence or a few at the left margin.
explain() { say "$out_width" 0 "" "$*"; }

# The layout every make aws-* command follows, so each kind of line is easy to
# tell apart. Blocks are separated by a blank line.
# - A step heading (step), with its explanation directly below it.
# - An explanation: a sentence at the left margin (explain), then bullets
#   (bullet), with any further sentence about a bullet under its text
#   (bullet_more), or numbered options (option).
# - What the script is doing: an -ing phrase ending in "..." (doing), with the
#   results of that work indented under it (check_ok, check_error), and any
#   detail of a result, such as a certificate's expiry, under its text (detail).
# - A question, indented (ask, confirm).
# - A status: indented under the question it answers (answer_ok, answer_warn),
#   or at the left margin when it stands alone (ok, warn).
# Every helper wraps its text when it's printed (say), so pass each message as
# one string, however long its values make it, and never wrap it by hand.
# The helpers can't enforce these rules, so follow them by hand:
# - Each question makes sense without the explanation above it, and says what
#   its answer leads to.
# - A question whose answer can delete data has no default (choose with no
#   DEFAULT), so a number has to be typed.
# - Each command that leaves the server running ends with what_next, followed
#   by the run record's path when the command writes one (finish_run).
# - Every command, errors included, ends with a blank line before the shell
#   prompt (fail, what_next and finish_run print it).
# - Each value is looked up once and reused, so a later lookup that fails
#   can't change it partway through a run.
bullet() { say "$out_width" 4 "  $dot " "$*"; }

# bullet_more TEXT: a further sentence about the bullet above, under its text.
bullet_more() { say "$out_width" 4 "    " "$*"; }

# option NUMBER TEXT: one of the numbered choices a choose question offers.
option() { say "$out_width" $((4 + ${#1})) "  $1) " "$2"; }

# doing TEXT: say what the script is doing now, such as "Checking the server".
doing() {
  echo
  say "$out_width" 0 "" "$*..."
}

# check_ok, check_warn and check_error TEXT: a result of the work the doing
# line above describes, indented under it.
check_ok() { say "$out_width" 6 "  ${green}ok:${reset} " "$*"; }
check_warn() { say "$err_width" 11 "  ${yellow}warning:${reset_err} " "$*" >&2; }
check_error() { say "$err_width" 9 "  ${red}error:${reset_err} " "$*" >&2; }

# detail TEXT: a detail of the result above it, such as a certificate's
# expiry, under the result's text and without an ok: of its own.
detail() { say "$out_width" 6 "      " "$*"; }

# prompt_line TEXT: print all but the last line of TEXT wrapped as a question,
# and set $prompt to that last line, for read -p, so a long question wraps like
# everything else and the answer goes after its last line. Like read -p, it
# prints to stderr, and only when the answer comes from a terminal.
prompt_line() {
  local index
  # Said once a run, before its first question, where someone is typing.
  if [ -t 0 ] && [ -z "${GROUNDLINE_ENTER_HINT:-}" ]; then
    export GROUNDLINE_ENTER_HINT=shown
    say "$err_width" 2 "  " "Press Enter to keep the value in [brackets]. Ctrl-C cancels, changing nothing." >&2
  fi
  wrap_text "$err_width" 2 "$1"
  if [ -t 0 ]; then
    for ((index = 0; index + 1 < ${#wrapped[@]}; index++)); do
      printf '  %s\n' "${wrapped[index]}" >&2
    done
  fi
  prompt="  ${wrapped[${#wrapped[@]} - 1]} "
}

# duration SECONDS: how long something took, such as "42s" or "1m 02s".
duration() {
  if [ "$1" -lt 60 ]; then
    echo "${1}s"
  else
    printf '%dm %02ds\n' $(($1 / 60)) $(($1 % 60))
  fi
}

# The run's log: everything the tools it runs print, such as Terraform's apply
# output, which the screen leaves out. It's in infra/runs/, which git ignores,
# and only you can read it, since Terraform's output names your email address
# and IP. One run, such as make aws-up with the aws-deploy it runs, has one log.
runs_dir=infra/runs

# --- The run record ---
#
# Each run of make aws-up, aws-deploy or aws-destroy leaves a record beside its
# log: infra/runs/<UTC time>_<account>_<deployment>_<command>.json. It says
# what was decided and where each answer came from (default, chosen or
# previous), the plan in plain words and whether it was approved, how the run
# ended, and the tool versions. It never holds a secret: for the Anthropic API
# key it says only whether it was kept, replaced or left out. Its path is the
# last thing every run prints, however it ends. The last 10 records of each
# deployment in each account are kept.
#
# While the run goes on, the record is a JSON file named by
# GROUNDLINE_RUN_STATE, which a script the run starts, such as aws-deploy run
# by make aws-up, adds to; the script you ran writes it out when it exits.

# on_exit COMMAND: run COMMAND when the script exits, after any added before.
# One EXIT trap runs them all, so adding one never replaces another.
exit_actions=()
on_exit() {
  exit_actions+=("$1")
  trap run_exit_actions EXIT
}
run_exit_actions() {
  exit_status=$?
  local action
  for action in ${exit_actions[@]+"${exit_actions[@]}"}; do
    eval "$action" || true
  done
}

# start_run: start this run's record, in the script you ran. In a script it
# runs, this does nothing, so both add to one record.
start_run() {
  [ -z "${GROUNDLINE_RUN_STATE:-}" ] || return 0
  local started command
  started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  command="${GROUNDLINE_AWS_COMMAND:-make $(basename "$0")}"
  GROUNDLINE_RUN_STATE="$(mktemp)"
  export GROUNDLINE_RUN_STATE
  jq -n --arg command "${command#make aws-}" --arg started "$started" --arg stamp "${started//:/-}" \
    '{format_version: "1.0", command: $command, started_at: $started, stamp: $stamp,
      decisions: {}, plan: {approved: false, summary: []}, result: {}}' >"$GROUNDLINE_RUN_STATE"
  on_exit finish_run
}

# record FILTER [JQ_ARGS...]: change this run's record with a jq filter.
record() {
  [ -n "${GROUNDLINE_RUN_STATE:-}" ] && [ -f "$GROUNDLINE_RUN_STATE" ] || return 0
  local filter="$1" updated
  shift
  updated="$(jq "$@" "$filter" "$GROUNDLINE_RUN_STATE")" \
    && printf '%s\n' "$updated" >"$GROUNDLINE_RUN_STATE"
}

# record_decision NAME VALUE SOURCE: record an answer and whether it was the
# default, chosen or kept from a previous run.
record_decision() { record '.decisions[$k] = {value: $v, source: $s}' --arg k "$1" --arg v "$2" --arg s "$3"; }

# record_result NAME VALUE: record something the run did, such as the commit
# the server runs.
record_result() { record '.result[$k] = $v' --arg k "$1" --arg v "$2"; }

# run_value NAME: a top-level value of this run's record, or nothing.
run_value() {
  [ -n "${GROUNDLINE_RUN_STATE:-}" ] && [ -f "$GROUNDLINE_RUN_STATE" ] || return 0
  jq -r --arg k "$1" '.[$k] // empty' "$GROUNDLINE_RUN_STATE"
}

# run_base: infra/runs/<time>_<account>_<deployment>_<command>, with _2 and
# so on added when a run already used it in the same second. The suffix starts
# with _, which sorts after the . of .json, so a later run's record still sorts
# after the earlier one's, as prune_runs needs.
run_base() {
  local stamp command name base next=2
  stamp="$(run_value stamp)"
  stamp="${stamp:-$(date -u +%Y-%m-%dT%H-%M-%SZ)}"
  command="${GROUNDLINE_AWS_COMMAND:-make $(basename "$0")}"
  command="${command#make aws-}"
  name="$(tfvars_value deployment_name)"
  base="$runs_dir/${stamp}_${account:-unknown}_${name:-new}_${command}"
  local candidate="$base"
  while [ -e "$candidate.json" ] || [ -e "$candidate.log" ]; do
    candidate="${base}_$next"
    next=$((next + 1))
  done
  echo "$candidate"
}

# ensure_run_log: set $run_log, creating this run's log the first time.
ensure_run_log() {
  run_log="$(run_value log_path)"
  [ -z "$run_log" ] || return 0
  mkdir -p "$runs_dir"
  run_log="$(run_base).log"
  (umask 077 && : >"$run_log")
  record '.log_path = $p' --arg p "$run_log"
}

# tool_version COMMAND...: the first line COMMAND prints, or nothing.
tool_version() { "$@" 2>/dev/null | head -n 1 || true; }

# finish_run: write this run's record and print its path, last. On exit only.
finish_run() {
  local status="${exit_status:-0}" outcome log path label terraform_version name
  [ -f "${GROUNDLINE_RUN_STATE:-}" ] || return 0
  outcome="$(run_value outcome)"
  if [ -z "$outcome" ]; then
    case "$status" in
      0) outcome=succeeded ;;
      130) outcome=canceled ;;
      *) outcome=failed ;;
    esac
  fi
  mkdir -p "$runs_dir"
  log="$(run_value log_path)"
  if [ -n "$log" ]; then path="${log%.log}.json"; else path="$(run_base).json"; fi
  terraform_version="$(tool_version terraform version -json | jq -r '.terraform_version // empty' 2>/dev/null || true)"
  name="$(tfvars_value deployment_name)"
  (umask 077 && jq --arg finished "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg outcome "$outcome" \
    --arg commit "$(tool_version git rev-parse --short HEAD)" --arg terraform "$terraform_version" \
    --arg aws_cli "$(tool_version aws --version | cut -d' ' -f1)" \
    --arg account "${account:-}" --arg region "${region:-}" --arg name "$name" \
    'del(.stamp) + {finished_at: $finished, outcome: $outcome,
      tool: {commit: $commit, terraform: $terraform, aws_cli: $aws_cli},
      project: {account: $account, region: $region}, deployment: $name}
      | .tool |= with_entries(select(.value != ""))' \
    "$GROUNDLINE_RUN_STATE" >"$path")
  rm -f "$GROUNDLINE_RUN_STATE"
  prune_runs "${account:-unknown}" "${name:-new}"
  label="Run record:"
  [ "$outcome" != failed ] || [ -z "$log" ] || label="Log and run record:"
  if [ "$outcome" = succeeded ]; then
    printf '%s %s\n\n' "$label" "$(file_link 1 "$path")"
  else
    printf '%s %s\n\n' "$label" "$(file_link 2 "$path")" >&2
  fi
}

# file_link FD PATH: PATH, as a link a terminal can open when FD is one that
# shows links (OSC 8); the path itself is always the text, so it can be copied.
file_link() {
  if [ -t "$1" ] && ! plain_terminal; then
    printf '\033]8;;file://%s/%s\033\\%s\033]8;;\033\\' "$PWD" "$2" "$2"
  else
    printf '%s' "$2"
  fi
}

# prune_runs ACCOUNT NAME: keep the last 10 records of deployment NAME in
# ACCOUNT, with their logs. The time comes first in each name, so they sort in
# order.
prune_runs() {
  local records=() record_file index
  for record_file in "$runs_dir"/*_"$1"_"$2"_*.json; do
    [ -e "$record_file" ] && records+=("$record_file")
  done
  for ((index = 0; index + 10 < ${#records[@]}; index++)); do
    rm -f -- "${records[index]}" "${records[index]%.json}.log"
  done
}

# run_logged DOING DONE NOTE COMMAND...: say "DOING...", run COMMAND with its
# output in the run's log, then say "ok: DONE (NOTE, 1m 02s)", leaving NOTE out
# when it's empty. If COMMAND fails, show the end of the log and where the
# whole of it is.
#
# Ctrl-C: COMMAND runs in a process group of its own, so the terminal's Ctrl-C
# reaches only this script, which passes each one on, once. Terraform finishes
# the change it's making on the first and stops at once on the second, so the
# first says it's stopping safely. Either way the run then stops, saying to run
# the command again, since Terraform picks up from what exists.
run_logged() {
  local doing_text="$1" done_text="$2" note="$3" started status pid saved_trap
  shift 3
  ensure_run_log
  doing "$doing_text"
  started=$SECONDS
  printf '\n$ %s\n' "$*" >>"$run_log"
  saved_trap="$(trap -p INT)"
  interrupts=0
  set -m
  "$@" >>"$run_log" 2>&1 </dev/null &
  pid=$!
  set +m
  trap 'interrupts=$((interrupts + 1)); stopping "$interrupts"; kill -INT "$pid" 2>/dev/null || true' INT
  while :; do
    status=0
    wait "$pid" || status=$?
    # A Ctrl-C interrupts wait, not COMMAND, so wait again until it ends.
    if [ "$status" -gt 128 ] && kill -0 "$pid" 2>/dev/null; then
      continue
    fi
    break
  done
  if [ -n "$saved_trap" ]; then eval "$saved_trap"; else trap - INT; fi
  # The run record's line, printed last, says where the whole log is.
  if [ "$interrupts" -gt 0 ]; then
    echo >&2
    error "canceled partway. Run ${GROUNDLINE_AWS_COMMAND:-it} again to carry on from what exists."
    echo >&2
    exit 130
  fi
  if [ "$status" != 0 ]; then
    explain "Its last lines, from the log:" >&2
    tail -n 30 "$run_log" | sed 's/^/    /' >&2
    fail "that didn't finish: see the error above."
  fi
  check_ok "$done_text (${note:+$note, }$(duration $((SECONDS - started))))"
}

# stopping COUNT: what the COUNTth Ctrl-C during run_logged does.
stopping() {
  echo >&2
  if [ "$1" = 1 ]; then
    check_warn "Stopping safely: Terraform is finishing the change it's making. Run ${GROUNDLINE_AWS_COMMAND:-it} again to carry on. Ctrl-C again stops it at once, which can leave a change half-made."
  else
    check_warn "Stopping at once."
  fi
}

# hint TEXT: why an answer wasn't accepted, indented like the question, which
# is asked again below it.
hint() { say "$out_width" 2 "  " "$*"; }

# ask PROMPT [DEFAULT]: read one line into $answer, or DEFAULT if it's empty.
ask() {
  local suffix="" prompt
  [ -n "${2:-}" ] && suffix=" [$2]"
  echo
  prompt_line "$1$suffix:"
  read -rp "$prompt" answer || fail "canceled: no answer to \"$1\""
  # For the run record: whether Enter kept the default.
  answer_source=chosen
  [ -n "$answer" ] || [ -z "${2:-}" ] || answer_source=default
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
    hint "Enter a number from 1 to $2."
  done
}

# confirm QUESTION: true if the answer is yes or empty. With no input left, such
# as when nobody is typing, it's false, so nothing is used or pushed unasked.
confirm() {
  local reply prompt
  echo
  prompt_line "$1 [Y/n]:"
  read -rp "$prompt" reply || return 1
  answer_source=chosen
  [ -n "$reply" ] || answer_source=default
  case "$reply" in "" | [Yy] | [Yy][Ee][Ss]) return 0 ;; *) return 1 ;; esac
}

# confirm_no QUESTION: like confirm, but Enter means no, for a choice that's
# easy to make by mistake, such as one that opens the browser.
confirm_no() {
  local reply prompt
  echo
  prompt_line "$1 [y/N]:"
  read -rp "$prompt" reply || return 1
  answer_source=chosen
  [ -n "$reply" ] || answer_source=default
  case "$reply" in [Yy] | [Yy][Ee][Ss]) return 0 ;; *) return 1 ;; esac
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
  local prompt
  echo
  explain "In your browser, sign in to the AWS project for your test deployment."
  bullet "Sign in at https://console.aws.amazon.com"
  bullet "No project yet? Create one at https://settings.aws.com > Project > Create project, then sign in to it."
  echo
  prompt_line "Press Enter once you're signed in to the project in your browser:"
  read -rp "$prompt" _ || fail "canceled: no project chosen"
  echo
  explain "Each project has its own Region. To copy it, in the console go to Manage projects > Overview > Additional Info > Region, and use its Copy button."
  while :; do
    ask "The project's Region code, such as us-east-2" "$current"
    [[ "$answer" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]+$ ]] && break
    hint "That isn't a Region code. Codes look like us-east-2 or eu-west-1."
  done
  aws configure set region "$answer" --profile "$AWS_PROFILE"
  echo
  explain "Next, your browser opens to give these scripts access to the project."
  bullet "Choose the session of the project you just signed in to."
  echo
  prompt_line "Press Enter to open the browser and choose the project's session:"
  read -rp "$prompt" _ || fail "canceled: no project chosen"
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

# The monthly prices quoted, as in us-east-1: a gp3 data volume, per GiB, and
# an Elastic IP, at $0.005 an hour. infra/README.md's Costs section breaks
# them down; update it too if they change.
# volume_cost [GIB], address_cost and kept_data_cost [GIB] print dollars, such
# as 4.05, for GIB (5 if not given).
volume_cost() { awk -v gib="${1:-5}" 'BEGIN { printf "%.2f", gib * 0.08 }'; }
address_cost() { echo 3.65; }
kept_data_cost() {
  awk -v volume="$(volume_cost "${1:-5}")" -v address="$(address_cost)" 'BEGIN { printf "%.2f", volume + address }'
}

# server_hourly_cost and running_monthly_cost: a running server, with its data
# volume and address, as the plan and What next quote it.
server_hourly_cost() { echo 0.024; }

# budget_threshold AMOUNT PERCENT: the dollars at which the budget alert
# (infra/budget.tf) emails, such as $17.50 for half of 35, or $35 for all of it.
budget_threshold() {
  awk -v amount="$1" -v percent="$2" 'BEGIN {
    value = amount * percent / 100
    if (value == int(value)) printf "$%d", value; else printf "$%.2f", value
  }'
}
running_monthly_cost() { echo 18; }

# optional_output NAME [ROOT]: output NAME of ROOT (infra by default), or
# nothing if there's no such output, as with nothing deployed. output, below,
# fails instead.
optional_output() { terraform -chdir="${2:-infra}" output -raw "$1" 2>/dev/null || true; }

# deployed_lifetime: the lifetime the deployment was made with, from its state.
# infra/data/ still says once make aws-destroy has kept a lasting deployment's
# data; with neither, the one in infra/terraform.tfvars.
deployed_lifetime() {
  local lifetime
  lifetime="$(optional_output lifetime)"
  lifetime="${lifetime:-$(optional_output lifetime infra/data)}"
  echo "${lifetime:-$(configured_lifetime)}"
}

# pre_upgrade_snapshots NAME: the ids of deployment NAME's pre-upgrade
# snapshots, one a line, sorted; nothing if there are none.
pre_upgrade_snapshots() {
  aws ec2 describe-snapshots --region "$region" --owner-ids self \
    --filters "Name=tag:deployment,Values=$1" Name=tag-key,Values=groundline-pre-upgrade \
    --query 'Snapshots[].SnapshotId' --output text \
    | tr '\t ' '\n\n' | { grep -v -e '^None$' -e '^$' || true; } | sort
}

# kept_snapshot NAME: "ID COMMIT" for deployment NAME's newest pre-upgrade
# snapshot, COMMIT being the short commit it was taken before upgrading to;
# nothing if there's none. Fails if AWS can't say.
kept_snapshot() {
  aws ec2 describe-snapshots --region "$region" --owner-ids self \
    --filters "Name=tag:deployment,Values=$1" Name=tag-key,Values=groundline-pre-upgrade \
    --output json \
    | jq -r '.Snapshots | sort_by(.StartTime) | last // empty
      | "\(.SnapshotId) \((.Tags // []) | map(select(.Key == "groundline-pre-upgrade"))[0].Value // "unknown")"'
}

# commit_change FROM TO: same, rollback (TO is older than FROM) or forward,
# which is also the answer when git doesn't know FROM. A lasting deployment
# takes a snapshot only before moving forward.
commit_change() {
  if [ "$1" = "$2" ]; then
    echo same
  elif git merge-base --is-ancestor "$2" "$1" 2>/dev/null; then
    echo rollback
  else
    echo forward
  fi
}

# review_or_cancel WARNING EFFECT: say why keeping the running server would
# leave the saved settings unapplied, and ask to review a plan or stop.
review_or_cancel() {
  answer_warn "$1 Keeping this server would not apply them."
  bullet "$2"
  option 1 "Review a deployment plan"
  option 2 "Cancel and leave the running server unchanged"
  choose "Review deployment changes? (1 review, 2 cancel)" 2
  [ "$answer" = 1 ] || fail "canceled: running server unchanged"
}

# init_roots: terraform init in both folders, printing Terraform's output only
# if it fails. A checkout that has never deployed from this version has no
# infra/data/.terraform, and destroying needs it as much as deploying does.
init_roots() {
  local root init_log
  for root in infra/data infra; do
    init_log="$(terraform -chdir="$root" init -input=false 2>&1)" || {
      echo "$init_log" >&2
      fail "terraform init failed in $root"
    }
  done
}

# saved_app_settings: the app_settings in infra/terraform.tfvars, as JSON with
# sorted keys, such as {"GROUNDLINE_MAX_SOURCE_CHARS":150000}; nothing, after
# Terraform's error, if they're unreadable or break modules/app-settings' rules.
# Terraform parses the file, since it's HCL.
saved_app_settings() {
  local encoded
  # Terraform's default is none, and console takes a few seconds.
  if ! grep --quiet '^[[:space:]]*app_settings[[:space:]]*=' infra/terraform.tfvars 2>/dev/null; then
    echo '{}'
    return
  fi
  encoded="$(echo 'jsonencode(var.app_settings)' | terraform -chdir=infra console)" || return 0
  jq -r . <<<"$encoded" 2>/dev/null | jq -cS . 2>/dev/null || true
}

# deployed_app_settings: the running server's app_settings, in the same form.
# {} when there's none, such as for a server deployed before app_settings.
deployed_app_settings() {
  local settings
  settings="$(terraform -chdir=infra output -json app_settings 2>/dev/null | jq -cS . 2>/dev/null || true)"
  [ -n "$settings" ] || settings='{}'
  echo "$settings"
}

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

# choose_access_mode [DEFAULT [current]]: ask who may reach the app and set
# $access_mode. DEFAULT is 1 (IP-restricted, for testing) or 2 (unrestricted,
# which a lasting deployment's callers need). It's marked as recommended,
# unless "current" says it's only the value the setting has now.
choose_access_mode() {
  local default="${1:-1}" first="" second=" (recommended)"
  if [ "$default" = 1 ]; then
    first=" (recommended)" second=""
  fi
  [ "${2:-}" != current ] || first="" second=""
  option 1 "Only this computer can reach it (IP-restricted)$first"
  option 2 "Open to any IP over HTTPS; checks need an API key (unrestricted)$second"
  bullet "Both use HTTPS with an automatic certificate."
  choose "Who can reach it? (1 only this computer, 2 any IP)" 2 "$default"
  access_mode=restricted
  [ "$answer" = 1 ] || access_mode=unrestricted
}

# choose_lifetime [DEFAULT]: ask whether the deployment is disposable or
# lasting, and set $lifetime. DEFAULT is 1 (disposable) or 2 (lasting). It's
# set once per folder: a folder that manages a lasting deployment manages only
# that one.
choose_lifetime() {
  option 1 "Disposable: for trying a commit. It terminates itself after 8 hours, and make aws-destroy deletes it with its data."
  option 2 "Lasting: for staging or production. It keeps running, and keeps its data, API keys, settings and address when you upgrade it, until you delete it."
  choose "Disposable or lasting? (1 disposable, 2 lasting)" 2 "${1:-1}"
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

# --- The settings summary: make aws-up's first run and make aws-settings ---
#
# Each setting is a row: project, name, email, lifetime, budget, access and
# branch. Its value is in s_<row> (s_branch is main or branch), and where it
# came from in src_<row>: default (guessed), chosen, or previous (saved by an
# earlier run), as the run record says. mark_<row> is set when changing the
# lifetime guessed the row again, until the summary has shown that.

# taken_names: the deployment names already used in this AWS project, one a
# line: by a server that's starting or running, or by a data volume that make
# aws-destroy kept. A new deployment with one of them would share its
# resources' names, and could pick up its kept data.
taken_names() {
  {
    running_lifetimes 2>/dev/null | cut -d' ' -f1 || true
    aws ec2 describe-volumes --region "$region" \
      --filters Name=tag:app,Values=groundline Name=tag:role,Values=data --output json 2>/dev/null \
      | jq -r '.Volumes[]? | (.Tags // [] | from_entries).deployment // empty' 2>/dev/null || true
  } | sort -u
}

# name_taken NAME: whether NAME is in $taken (from taken_names).
name_taken() { grep -qxF -- "$1" <<<"${taken:-}"; }

# free_name BASE: BASE, or BASE-2, BASE-3 and so on, whichever isn't taken,
# shortened to keep within the 20 characters a name can have.
free_name() {
  local base="$1" candidate="$1" number=1
  while name_taken "$candidate"; do
    number=$((number + 1))
    candidate="${base:0:$((19 - ${#number}))}-$number"
  done
  echo "$candidate"
}

# guess_name LIFETIME: set s_name to the name to suggest: production for a
# lasting deployment, or your username for a disposable one, made free with
# free_name. s_name_taken is set to the name it had to avoid, if any.
guess_name() {
  local base=production
  if [ "$1" != lasting ]; then
    base="$(id -un | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9-' | cut -c1-20)"
    [[ "$base" =~ ^[a-z] ]] || base=test
  fi
  s_name="$(free_name "$base")"
  s_name_taken=""
  [ "$s_name" = "$base" ] || s_name_taken="$base"
}

# on_main: whether the checkout is on main, or on no branch, which deploys
# main too.
on_main() { [ -z "$branch" ] || [ "$branch" = main ]; }

# guess_settings: fill every row with its guess, from the AWS sign-in, git's
# settings and the branch. On main it's a lasting deployment any IP can reach,
# for callers; on another branch, a disposable one for trying it. Needs
# $branch and $taken.
guess_settings() {
  s_lifetime=disposable s_lifetime_why="you're on $branch"
  if [ "$branch" = main ]; then
    s_lifetime=lasting s_lifetime_why="you're on main"
  elif [ -z "$branch" ]; then
    s_lifetime_why="you're not on a branch"
  fi
  # Empty without git's setting: make aws-up asks for it then.
  s_email="$(git config user.email || true)" s_email_why="git config user.email"
  src_project=default src_name=default src_email=default src_lifetime=default
  src_budget=default src_access=default src_branch=default
  s_name="" s_access="" s_budget="" s_branch=""
  guess_for_lifetime
  mark_name="" mark_access="" mark_budget="" mark_branch=""
}

# guess_for_lifetime: guess again each row that follows the lifetime and still
# holds a guess, marking those that change. Rows you chose stay as they are.
guess_for_lifetime() {
  local before
  if [ "$src_name" = default ]; then
    before="${s_name:-}"
    guess_name "$s_lifetime"
    [ -z "$before" ] || [ "$s_name" = "$before" ] || mark_name=yes
  fi
  guess_row access "$([ "$s_lifetime" = lasting ] && echo unrestricted || echo restricted)"
  guess_row budget "$([ "$s_lifetime" = lasting ] && echo 35 || echo 5)"
  guess_row branch "$({ [ "$s_lifetime" = lasting ] || on_main; } && echo main || echo branch)"
}

# guess_row ROW VALUE: set s_ROW to VALUE if it holds a guess, marking it if it
# had another one.
guess_row() {
  local value_var="s_$1" source_var="src_$1"
  [ "${!source_var}" = default ] || return 0
  if [ -n "${!value_var:-}" ] && [ "${!value_var}" != "$2" ]; then
    printf -v "mark_$1" yes
  fi
  printf -v "s_$1" '%s' "$2"
}

# load_settings: fill every row from infra/terraform.tfvars, as previous.
load_settings() {
  s_name="$(deployment_name)" s_email="$(tfvars_value budget_alert_email)"
  s_lifetime="$(configured_lifetime)" s_budget="$(tfvars_value budget_amount)"
  s_access="$(configured_access_mode)" s_project_why="" s_name_taken="" s_lifetime_why=""
  [ -n "$s_budget" ] || s_budget="$([ "$s_lifetime" = lasting ] && echo 35 || echo 5)"
  src_project=previous src_name=previous src_email=previous src_lifetime=previous
  src_budget=previous src_access=previous src_branch=previous
  mark_name="" mark_access="" mark_budget="" mark_branch=""
}

# summary_label ROW: the row's name in the summary.
summary_label() {
  case "$1" in
    project) echo "AWS project" ;;
    name) echo "Deployment name" ;;
    email) echo "Email alerts to" ;;
    lifetime) echo "Lifetime" ;;
    budget) echo "Budget" ;;
    access) echo "Access" ;;
    branch) echo "Branch" ;;
  esac
}

# summary_text ROW: the row's value in words, with where a guess came from.
summary_text() {
  local source_var="src_$1" mark_var="mark_$1" text note="" source
  source="${!source_var}"
  case "$1" in
    project)
      text="$(project_label), in $region"
      note="${s_project_why:-}"
      ;;
    name)
      text="$s_name"
      if [ "$source" = default ] && [ -n "$s_name_taken" ]; then
        note="$s_name_taken is already used in this project"
      elif [ "$source" = default ] && [ -z "${!mark_var:-}" ]; then
        note="names its AWS resources, such as groundline-$s_name"
      fi
      ;;
    email)
      text="$s_email"
      [ "$source" != default ] || note="$s_email_why"
      ;;
    lifetime)
      if [ "$s_lifetime" = lasting ]; then
        text="lasting: keeps running, with its data, until you delete it"
      else
        text="disposable: a test deployment that terminates itself, with its data, 8 hours after it starts"
      fi
      [ "$source" != default ] || [ -z "${s_lifetime_why:-}" ] || note="guessed because $s_lifetime_why"
      ;;
    budget)
      text="$(budget_threshold "$s_budget" 100) a month: you'll get an email when this month's AWS costs reach $(budget_threshold "$s_budget" 50), and again at $(budget_threshold "$s_budget" 100)"
      ;;
    access)
      if [ "$s_access" = restricted ]; then
        text="only this computer can reach it (IP-restricted)"
      else
        text="open to any IP over HTTPS; checks need an API key"
      fi
      ;;
    branch)
      if [ "$s_branch" = main ]; then
        text="main, from groundline-dev/groundline"
      else
        text="your branch $branch, at your last commit ($(git rev-parse --short HEAD))"
      fi
      ;;
  esac
  [ -z "${!mark_var:-}" ] || note="changed to match the lifetime${note:+; $note}"
  echo "$text${note:+ ($note)}"
}

# summary_row LEAD LABEL TEXT: one row, LEAD being its number or - for a row
# that can't change. Labels make a column when there's room for one.
summary_row() {
  local lead="  $1) "
  [ "$1" != - ] || lead="  $dot  "
  if [ "$out_width" -ge 60 ]; then
    say "$out_width" 22 "$lead$(printf '%-17s' "$2")" "$3"
  else
    say "$out_width" 5 "$lead" "$2: $3"
  fi
}

# review_summary INTRO ROWS [LOCKED_ROWS]: show the rows under INTRO, numbered,
# and LOCKED_ROWS, which can't change, without numbers; let the developer
# change rows by number until Enter keeps them all.
review_summary() {
  local intro="$1" key number count
  local -a keys
  set -f
  # shellcheck disable=SC2206  # one row a word; globbing is off
  keys=($2)
  set +f
  count="${#keys[@]}"
  while :; do
    echo
    explain "$intro"
    for key in ${3:-}; do
      summary_row - "$(summary_label "$key")" "$(summary_text "$key") (can't change once the deployment exists)"
    done
    number=0
    for key in "${keys[@]}"; do
      number=$((number + 1))
      summary_row "$number" "$(summary_label "$key")" "$(summary_text "$key")"
      printf -v "mark_$key" ''
    done
    intro="Here they are with your change:"
    while :; do
      ask "Change one by number, or press Enter to keep them all"
      [ -n "$answer" ] || return 0
      [[ "$answer" =~ ^[0-9]+$ ]] && [ "$answer" -ge 1 ] && [ "$answer" -le "$count" ] && break
      hint "Enter a number from 1 to $count, or press Enter."
    done
    edit_setting "${keys[answer - 1]}"
  done
}

# edit_setting ROW: ask for the row's new value. Enter keeps the one it has.
edit_setting() {
  case "$1" in
    project)
      confirm_no "Sign in to a different AWS project? Your browser opens to choose it." || return 0
      choose_project
      use_project
      s_project_why="the project you just signed in to" src_project=chosen
      taken="$(taken_names)"
      if [ "$src_name" = default ]; then
        guess_name "$s_lifetime"
      elif name_taken "$s_name"; then
        answer_warn "$s_name is already used in $(project_label), so the name is a guess again."
        src_name=default
        guess_name "$s_lifetime"
      fi
      ;;
    name)
      echo
      explain "The deployment name goes into its AWS resources' names, such as groundline-$s_name. It has to be unique in the AWS project, and can't change once the deployment exists."
      while :; do
        ask "Deployment name" "$s_name"
        if ! [[ "$answer" =~ ^[a-z][a-z0-9-]{0,19}$ ]]; then
          hint "Use 1 to 20 lowercase letters, digits and hyphens, starting with a letter."
        elif name_taken "$answer"; then
          hint "$answer is already used in this project: a server or kept data still has that name. Choose another, such as $(free_name "$answer")."
        else
          break
        fi
      done
      [ "$answer" = "$s_name" ] || { s_name="$answer" src_name=chosen s_name_taken=""; }
      ;;
    email)
      while :; do
        ask "Email alerts to" "$s_email"
        [[ "$answer" =~ ^[^@[:space:]\"\\]+@[^@[:space:]\"\\]+\.[^@[:space:]\"\\]+$ ]] && break
        hint "That doesn't look like an email address."
      done
      [ "$answer" = "$s_email" ] || { s_email="$answer" src_email=chosen; }
      ;;
    lifetime)
      echo
      choose_lifetime "$([ "$s_lifetime" = lasting ] && echo 2 || echo 1)"
      [ "$lifetime" != "$s_lifetime" ] || return 0
      s_lifetime="$lifetime" src_lifetime=chosen
      guess_for_lifetime
      ;;
    budget)
      echo
      explain "The budget alert covers the whole AWS project, so a lasting deployment is best in a project of its own."
      while :; do
        ask "Monthly budget alert, in US dollars" "$s_budget"
        # $1,000 and 1 000 mean 1000.
        answer="$(printf '%s' "$answer" | tr -d '$, ')"
        [[ "$answer" =~ ^[1-9][0-9]{0,4}$ ]] && break
        hint "Enter a whole number of dollars up to \$99,999, such as 35."
      done
      [ "$answer" = "$s_budget" ] || { s_budget="$answer" src_budget=chosen; }
      ;;
    access)
      echo
      choose_access_mode "$([ "$s_access" = unrestricted ] && echo 2 || echo 1)" current
      [ "$access_mode" = "$s_access" ] || { s_access="$access_mode" src_access=chosen; }
      ;;
    branch)
      if on_main; then
        echo
        explain "You're on main, so main is the only branch to deploy from this checkout. Once it runs, make aws-upgrade TO=<commit> deploys a specific commit."
        return 0
      fi
      echo
      option 1 "main, from groundline-dev/groundline"
      option 2 "your branch $branch, at your last commit ($(git rev-parse --short HEAD))"
      bullet "The server clones the branch's last pushed commit from GitHub, so uncommitted changes aren't included."
      choose "Which branch should the server run? (1 main, 2 your branch)" 2 "$([ "$s_branch" = main ] && echo 1 || echo 2)"
      answer="$([ "$answer" = 1 ] && echo main || echo branch)"
      [ "$answer" = "$s_branch" ] || { s_branch="$answer" src_branch=chosen; }
      ;;
  esac
}

# lasting_branch_guard NAME BRANCH STATE: a lasting deployment's callers rely on
# it, so a branch goes to it only once its name is typed. STATE says what
# exists, for the message if it isn't: running, kept-data, or none.
lasting_branch_guard() {
  echo
  explain "Callers rely on the lasting deployment $1. Deploying $2 puts code that isn't merged into main in front of them."
  ask "To deploy $2 to it anyway, type the deployment's name ($1)"
  [ "$answer" = "$1" ] || fail "$(canceled_message "$1" "$3")"
}

# canceled_message NAME STATE: what stopping now leaves, from what exists:
# running (a server), kept-data (data make aws-destroy kept), or none.
canceled_message() {
  case "$2" in
    running) echo "canceled: $1 keeps running what it runs now" ;;
    kept-data) echo "canceled: no server was started. $1's data and address are unchanged." ;;
    *) echo "canceled: nothing was created." ;;
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

# store_pending_key: store the Anthropic API key make aws-up handed this script
# on file descriptor 3, once. aws-deploy calls it once the server's plan is
# approved, before Terraform applies it, so cancelling stores nothing, and
# cancelling a run that would replace a key keeps the old one. With nothing to
# approve, it's stored after the apply. The key goes from the pipe to the AWS
# CLI's stdin: never in an argument, an environment variable or a file.
store_pending_key() {
  [ "${GROUNDLINE_KEY_ON_FD3:-}" = yes ] && [ -z "${key_stored:-}" ] || return 0
  key_stored=yes
  local key
  key="$(cat <&3)"
  exec 3<&-
  doing "Storing your Anthropic API key"
  # printf is a shell builtin, so the key isn't in any process's arguments.
  printf '%s' "$key" | aws ssm put-parameter --region "$region" --name "$anthropic_parameter" \
    --type SecureString --value file:///dev/stdin ${GROUNDLINE_KEY_OVERWRITE:+--overwrite} >/dev/null \
    || fail "couldn't store your Anthropic API key in $anthropic_parameter, so nothing was changed"
  unset key
  if [ "$lifetime" = lasting ]; then
    check_ok "stored it in $(project_label)'s Parameter Store; make aws-destroy DATA=delete deletes it"
  else
    check_ok "stored it in $(project_label)'s Parameter Store; make aws-destroy deletes it"
  fi
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

# shared_project_warning NAME LIFETIME: warn when deployment NAME shares this
# project with deployments of the other kind. Budget alerts cover the whole
# project, so a lasting deployment's cost sets off the $5 disposable alerts,
# and theirs counts toward its budget. It warns, since some have only one
# project.
shared_project_warning() {
  local name="$1" lifetime="$2" running="$3" others lasting
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
    explain "You also have a test deployment in account $other."
    bullet "To check it or delete it, run make aws-project and choose that project, then make aws-status or make aws-destroy."
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
  local name="$1" lifetime="$2" held_account held_name held_lifetime
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
  say "$err_width" 0 "" "Terminating removed the server, its data and its address. Run make aws-destroy to remove the security group, IAM role, instance profile and budget it left behind, or make aws-deploy for a new server." >&2
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
  explain "To try your server, at $app_url:"
  bullet "make aws-key creates an API key, for trying requests at $app_url/docs."
  bullet "make aws-smoke-live sends one real commission check, which costs under a cent."
  mode="$(optional_output access_mode)"
  case "$mode" in
    restricted) bullet "IP-restricted HTTPS: only the configured tester IP can access application routes, including health and docs." ;;
    unrestricted) bullet "Unrestricted HTTPS: any caller IP can reach health/docs; protected endpoints require an API key." ;;
  esac
  bullet "To call it from your app's backend, use $app_url as the base URL and an API key from make aws-key. See docs/deploying-to-aws.md, \"Calling it from your app\"."
  echo
  explain "What it costs, and how to change or stop it:"
  if [ "$(deployed_lifetime)" = lasting ]; then
    bullet "It costs about \$$(running_monthly_cost) a month while it runs. It's a lasting deployment, so it has no time limit."
    bullet "make aws-up upgrades it to another commit or settings. It keeps its data and address."
    bullet "make aws-destroy deletes the server and keeps its data and address, for about \$$(kept_data_cost "$(optional_output data_volume_gib infra/data)") a month; make aws-destroy DATA=delete deletes those too."
    echo
    return
  fi
  hours="$(terraform -chdir=infra output -raw auto_terminate_hours 2>/dev/null || echo 8)"
  [ "$deadline" = none ] || bullet "It costs about \$$(server_hourly_cost) an hour while it runs."
  case "$deadline" in
    "")
      bullet "make aws-status shows when it terminates itself."
      bullet "make aws-reset-deadline moves that to $hours hours from now."
      ;;
    none) bullet "It has no time limit, so it costs about \$$(running_monthly_cost) a month." ;;
    *)
      bullet "It terminates itself, with its data, at $(describe_deadline "$deadline")."
      bullet "make aws-reset-deadline moves that to $hours hours from now."
      ;;
  esac
  bullet "make aws-destroy deletes it, with its data and your Anthropic API key. Run it even after the server terminates itself, to remove what it leaves behind."
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
  if [ -z "$from" ] || [ -z "$to" ] || [ "$from" = "$to" ]; then
    return 0
  fi
  bullet_more "It's upgraded from ${from:0:7} to ${to:0:7}."
  if [ "$(commit_change "$from" "$to")" = rollback ]; then
    bullet_more "${to:0:7} is older than ${from:0:7}, so this rolls it back. If a database migration ran since, restore the pre-upgrade snapshot first (docs/deploying-to-aws.md)."
  fi
}

# describe_settings PLAN_JSON: the app settings an update of the server
# changes, one a line, from its groundline-setting-<NAME> tags.
describe_settings() {
  local change
  while IFS= read -r change; do bullet_more "$change"; done < <(jq -r '.resource_changes[]
    | select(.type == "aws_instance" and .change.actions == ["update"])
    | (.change.before.tags // {}) as $before | (.change.after.tags // {}) as $after
    | ([$before, $after] | map(keys) | add | unique)[]
    | select(startswith("groundline-setting-")) as $tag
    | select($before[$tag] != $after[$tag])
    | ($tag | ltrimstr("groundline-setting-")) as $name
    | if $after[$tag] == null then "Its \($name) goes back to the default (it was \($before[$tag]))."
      elif $before[$tag] == null then "Its \($name) becomes \($after[$tag]) (it was the default)."
      else "Its \($name) becomes \($after[$tag]) (it was \($before[$tag]))." end' <<<"$1")
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
  local hours short="${commit:-}" created="" updated="" deleted=""
  short="${short:0:7}"
  hours="$(jq -r '.variables.auto_terminate_hours.value // 8' <<<"$1")"
  # describe_limit: the server's time limit, under the bullet that creates it.
  describe_limit() {
    if [ "$hours" = 0 ]; then
      bullet_more "It has no time limit, so it runs until make aws-destroy."
    else
      bullet_more "It terminates itself, with its data, $hours hours after it starts. You can also delete it manually with make aws-destroy."
    fi
  }
  while IFS='|' read -r type actions id old_cidr new_cidr old_port new_port size address old_size; do
    case "$type:$actions" in
      aws_ebs_volume:create)
        bullet "create its data volume ($size GiB, about \$$(volume_cost "$size") a month), which keeps the database, its API keys and the HTTPS certificate when the server is replaced."
        ;;
      aws_ebs_volume:update)
        if [ -n "$old_size" ] && [ "$size" != "$old_size" ]; then
          bullet "grow its data volume from $old_size to $size GiB, about \$$(volume_cost "$size") a month. Its filesystem grows when the server is upgraded. A volume can't shrink again."
        else
          updated+="$(friendly_name "$type")"$'\n'
        fi
        ;;
      aws_eip:create)
        bullet "reserve its address, an Elastic IP, which stays the same when the server is replaced. It costs about \$$(address_cost) a month, in place of the server's own address."
        ;;
      aws_ebs_volume:delete | aws_ebs_volume:delete+create | aws_ebs_volume:create+delete)
        bullet "delete its data volume ($id): the database, its API keys and the HTTPS certificate."
        ;;
      aws_eip:delete | aws_eip:delete+create | aws_eip:create+delete)
        bullet "release its address${address:+, $address}. Callers would need a new one."
        ;;
      aws_instance:create)
        if [ -n "${previous_instance:-}" ]; then
          bullet "create a new server running $short, for about \$$(server_hourly_cost) an hour, in place of $previous_instance, which has terminated."
        else
          bullet "create your server, running $short, for about \$$(server_hourly_cost) an hour."
        fi
        describe_limit
        ;;
      aws_instance:delete+create | aws_instance:create+delete)
        bullet "replace your server ($id) with a new one running $short. Its data volume and address move to the new server, so it keeps its database, API keys and address."
        bullet_more "Terraform shows \"Destroying...\" for a few minutes while the old one stops. The new server costs about \$$(server_hourly_cost) an hour."
        describe_limit
        ;;
      aws_instance:update)
        bullet "update your server ($id) in place. It keeps its data."
        describe_commit "$1"
        describe_settings "$1"
        ;;
      aws_instance:delete) bullet "delete your server ($id)." ;;
      aws_vpc_security_group_ingress_rule:update | aws_vpc_security_group_ingress_rule:delete+create)
        bullet "let requests from ${new_cidr%/32} reach the server on TCP $new_port, in place of ${old_cidr%/32} on TCP $old_port."
        ;;
      *:create) created+="$(friendly_name "$type")"$'\n' ;;
      *:update) updated+="$(friendly_name "$type")"$'\n' ;;
      *:delete) deleted+="$(friendly_name "$type")"$'\n' ;;
      *) bullet "replace its $(friendly_name "$type") ($id)." ;;
    esac
  done < <(jq -r '.resource_changes[]
    | select(.mode == "managed" and .change.actions != ["no-op"])
    | [.type, (.change.actions | join("+")), (.change.before.id // ""), (.change.before.cidr_ipv4 // ""), (.change.after.cidr_ipv4 // ""), (.change.before.from_port // ""), (.change.after.from_port // ""), ((.change.after.size // .change.before.size // "") | tostring), (.change.before.public_ip // ""), ((.change.before.size // "") | tostring)]
    | join("|")' <<<"$1")
  # Everything here except the server, the data volume and the address is free;
  # update this if that changes.
  [ -z "$created" ] || bullet "create its $(printf %s "$created" | join_names), which are free."
  [ -z "$updated" ] || bullet "update its $(printf %s "$updated" | join_names)."
  [ -z "$deleted" ] || bullet "delete its $(printf %s "$deleted" | join_names)."
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
  explain "Terraform's plan, short version (FULL_PLAN=1 shows all of it):"
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
  on_exit "rm -rf -- '$plan_dir'"
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
  plan_label "$root" "$plan_json" >"$plan.label"
  # The same summary for the run record, a sentence a line, unwrapped.
  (out_width=100000 && describe_plan "$plan_json") \
    | sed -e 's/^  [·-] //' -e 's/^    //' | jq -R . | jq -s . >"$plan.record"
  planned="$plan"
}

# plan_label ROOT PLAN_JSON: what applying the plan does, as three lines for
# run_logged: the -ing phrase, the past tense, and how many resources it
# changes, so each Terraform folder's apply is a step of its own, such as
# "Deleting the server and what it uses".
plan_label() {
  local root="$1" changes count server deleting
  changes='[.resource_changes[]? | select(.mode == "managed" and .change.actions != ["no-op"])]'
  count="$(jq "$changes | length" <<<"$2")"
  deleting="$(jq "$changes | all(.change.actions == [\"delete\"])" <<<"$2")"
  server="$(jq -r "[$changes[] | select(.type == \"aws_instance\") | .change.actions | join(\"+\")][0] // \"\"" <<<"$2")"
  if [ "$deleting" = true ] && [ "$root" = infra/data ]; then
    printf '%s\n' "Deleting the saved data and address" "deleted the saved data and address"
  elif [ "$deleting" = true ]; then
    printf '%s\n' "Deleting the server and what it uses" "deleted the server and what it uses"
  elif [ "$root" = infra/data ]; then
    printf '%s\n' "Setting up the data volume and address" "set up the data volume and address"
  else
    case "$server" in
      create) printf '%s\n' "Creating your server" "created your server" ;;
      delete+create | create+delete) printf '%s\n' "Replacing your server" "replaced your server" ;;
      update) printf '%s\n' "Applying your settings to the server" "applied them to the server" ;;
      *) printf '%s\n' "Changing the server's AWS resources" "changed them" ;;
    esac
  fi
  if [ "$count" = 1 ]; then echo "1 resource"; else echo "$count resources"; fi
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
  explain "Making these changes will:"
  echo "$summary"
  # No default: applying can delete the server and its data, so Enter alone
  # doesn't approve it.
  echo
  option 1 "Make these changes"
  option 2 "Show Terraform's plan first"
  option 3 "Cancel, changing nothing"
  choose "Make the changes above? (1 make them, 2 show the plan, 3 cancel)" 3
  if [ "$answer" = 2 ]; then
    for ((index = 0; index + 1 < ${#pairs[@]}; index += 2)); do
      [ -z "${pairs[index + 1]}" ] || show_plan "${pairs[index]}" "${pairs[index + 1]}"
    done
    echo
    option 1 "Make these changes"
    option 2 "Cancel, changing nothing"
    choose "Make the changes above? (1 make them, 2 cancel)" 2
    [ "$answer" = 1 ] || answer=3
  fi
  # The run record keeps the plan, approved or not.
  for ((index = 0; index + 1 < ${#pairs[@]}; index += 2)); do
    [ -z "${pairs[index + 1]}" ] \
      || record '.plan.summary += $lines[0]' --slurpfile lines "${pairs[index + 1]}.record"
  done
  if [ "$answer" = 1 ]; then
    record '.plan.approved = true'
  else
    record '.plan.approved = false'
  fi
  [ "$answer" = 1 ] || fail "${cancel_message:-canceled: nothing changed}"
  plan_changed=yes
  # What has to wait for approval but come before the apply, such as storing
  # the Anthropic API key (store_pending_key).
  [ -z "${before_apply:-}" ] || "$before_apply"
  # Each folder's apply is a step of its own, named for what it does, with
  # Terraform's output in the run's log.
  local label=() line
  for ((index = 0; index + 1 < ${#pairs[@]}; index += 2)); do
    [ -n "${pairs[index + 1]}" ] || continue
    label=()
    while IFS= read -r line; do label+=("$line"); done <"${pairs[index + 1]}.label"
    run_logged "${label[0]}" "${label[1]}" "${label[2]}" \
      terraform -chdir="${pairs[index]}" apply -input=false -no-color "${pairs[index + 1]}"
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
# A line indented by two spaces is a detail of the result above it.
check_results() {
  local result
  while IFS= read -r result; do
    case "$result" in
      "  "*) detail "$result" ;;
      *) check_ok "$result" ;;
    esac
  done
}
