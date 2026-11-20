"""The layout helpers in scripts/aws-common.sh wrap every line when it's printed.

Messages have values filled in at run time, such as a project name or a path, so
lines wrapped by hand ran long. Each helper now wraps at the terminal's width or
78 columns, whichever is smaller, with later lines indented to line up with the
text, and never breaks a word, so a path or URL stays whole. Colour is added
after wrapping, and NO_COLOR or TERM=dumb turns it off.

These run the helpers with the bash on PATH, and with macOS's /bin/bash 3.2 too
where it's there, since that's what many developers' make runs.
"""

import os
import pty
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

LONG = (
    "Your deployment production will run in the AWS project groundline-dev "
    "(123456789012), in us-east-2, and everyone with access to it can read the "
    "Anthropic API key stored as /groundline/production/anthropic-api-key."
)
PATH_TOKEN = (
    "infra/runs/" + "2026-11-18T14-02-11Z_123456789012_production_up" * 2 + ".json"
)

# Each helper, and how far its later lines are indented.
HELPERS = {
    "explain": 0,
    "ok": 4,
    "warn": 9,
    "error": 7,
    "bullet": 4,
    "bullet_more": 4,
    "check_ok": 6,
    "check_warn": 11,
    "check_error": 9,
    "detail": 6,
    "hint": 2,
    "answer_ok": 6,
    "answer_warn": 11,
    "step": 0,
    "doing": 0,
}


def shells() -> list[str]:
    found = [shutil.which("bash") or "bash"]
    if Path("/bin/bash").exists() and os.path.realpath("/bin/bash") != os.path.realpath(
        found[0]
    ):
        found.append("/bin/bash")
    return found


def environment(**overrides: str) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("COLUMNS", "NO_COLOR")
    }
    env.pop("GROUNDLINE_AWS_SCRIPT", None)
    env.update({"LC_ALL": "en_US.UTF-8", "TERM": "xterm-256color"})
    env.update(overrides)
    return env


def run(shell: str, body: str, **env: str) -> str:
    """Run BODY after sourcing aws-common.sh, with stdout and stderr together."""
    result = subprocess.run(
        [shell, "-c", f"source scripts/aws-common.sh\n{body}"],
        cwd=ROOT,
        env=environment(**env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )
    return result.stdout


def run_in_terminal(shell: str, body: str, **env: str) -> str:
    """Like run, but with stdout and stderr on a pseudo-terminal, so colour is on."""
    return terminal(shell, body, False, env)


def run_typing(shell: str, body: str, **env: str) -> str:
    """Like run_in_terminal, with stdin on it too, as when someone is typing."""
    return terminal(shell, body, True, env)


def terminal(shell: str, body: str, typing: bool, env: dict[str, str]) -> str:
    leader, follower = pty.openpty()
    try:
        process = subprocess.Popen(
            [shell, "-c", f"source scripts/aws-common.sh\n{body}"],
            cwd=ROOT,
            env=environment(**env),
            stdin=follower if typing else subprocess.DEVNULL,
            stdout=follower,
            stderr=follower,
        )
        # Read while it runs: on macOS, what's left unread when the last
        # writer closes the terminal is lost.
        os.close(follower)
        follower = -1
        chunks: list[bytes] = []
        while True:
            try:
                chunk = os.read(leader, 65536)
            except OSError:  # EIO: every writer has closed the terminal
                break
            if not chunk:
                break
            chunks.append(chunk)
        assert process.wait() == 0
        return b"".join(chunks).decode().replace("\r\n", "\n")
    finally:
        if follower != -1:
            os.close(follower)
        os.close(leader)


def printed_lines(output: str) -> list[str]:
    return [line for line in output.split("\n") if line.strip()]


def fits(line: str, width: int, hang: int) -> bool:
    """Whether LINE fits WIDTH, or is one word too long for any line, at its indent."""
    if len(line) <= width:
        return True
    return line[:hang].strip() == "" and " " not in line[hang:]


@pytest.mark.parametrize("shell", shells())
@pytest.mark.parametrize("columns", [40, 60, 80, 120])
@pytest.mark.parametrize("helper", list(HELPERS))
def test_every_helper_wraps_at_the_width_with_a_hanging_indent(
    shell: str, columns: int, helper: str
):
    output = run(shell, f'{helper} "{LONG}"', COLUMNS=str(columns))
    lines = printed_lines(output)
    width = min(columns, 78)
    hang = HELPERS[helper]
    assert len(lines) > 1, output
    for line in lines:
        assert fits(line, width, hang), (line, len(line))
    for line in lines[1:]:
        assert line[:hang] == " " * hang and line[hang] != " ", (helper, line)
    # Nothing was lost or reordered.
    assert LONG in " ".join(line.strip() for line in lines), output


@pytest.mark.parametrize("shell", shells())
def test_options_line_up_under_their_text(shell: str):
    output = run(shell, f'option 1 "{LONG}"', COLUMNS="60")
    lines = printed_lines(output)
    assert lines[0].startswith("  1) ")
    assert all(line.startswith("     ") and line[5] != " " for line in lines[1:]), (
        output
    )


@pytest.mark.parametrize("shell", shells())
@pytest.mark.parametrize("columns,hang", [(40, 5), (59, 5), (60, 22), (80, 22)])
def test_summary_rows_make_a_column_when_there_is_room(
    shell: str, columns: int, hang: int
):
    output = run(
        shell,
        f'summary_row 2 "Deployment name" "{LONG}"; summary_row - Lifetime "{LONG}"',
        COLUMNS=str(columns),
    )
    lines = printed_lines(output)
    starts = [index for index, line in enumerate(lines) if line[2] in "2·"]
    assert len(starts) == 2, output
    assert lines[0].startswith("  2) Deployment name")
    assert lines[starts[1]].startswith("  ·  Lifetime")
    for index, line in enumerate(lines):
        assert fits(line, min(columns, 78), hang), (line, len(line))
        if index not in starts:
            assert line[:hang] == " " * hang and line[hang] != " ", (columns, line)


@pytest.mark.parametrize("shell", shells())
def test_without_a_terminal_it_wraps_at_78(shell: str):
    output = run(shell, f'explain "{LONG} {LONG}"')
    lines = printed_lines(output)
    assert max(len(line) for line in lines) <= 78
    assert max(len(line) for line in lines) > 60, "it shouldn't wrap narrower than 78"


@pytest.mark.parametrize("shell", shells())
def test_a_path_is_never_broken(shell: str):
    output = run(
        shell,
        f'bullet "The run record is {PATH_TOKEN}, kept with the others."',
        COLUMNS="60",
    )
    lines = printed_lines(output)
    holding = [line for line in lines if PATH_TOKEN in line]
    assert len(holding) == 1, output
    # Too long for any line, it has a line of its own, at the bullet's indent,
    # with the comma that follows it.
    assert holding[0] == "    " + PATH_TOKEN + ","


@pytest.mark.parametrize("shell", shells())
def test_a_token_that_fits_on_its_own_line_moves_to_the_next_line(shell: str):
    fingerprint = "a" * 64
    output = run(shell, f'detail "SHA-256 fingerprint: {fingerprint}"')
    assert printed_lines(output) == [
        "      SHA-256 fingerprint:",
        "      " + fingerprint,
    ]


@pytest.mark.parametrize("shell", shells())
def test_colour_is_added_after_wrapping(shell: str):
    output = run_in_terminal(
        shell, f'ok "{LONG}"; warn "{LONG}"; step "{LONG}"', COLUMNS="40"
    )
    assert ANSI.search(output), "colour should be on in a terminal"
    for line in printed_lines(output):
        plain = ANSI.sub("", line)
        assert len(plain) <= 40 or " " not in plain.strip(), line


@pytest.mark.parametrize("shell", shells())
@pytest.mark.parametrize("setting", [{"NO_COLOR": "1"}, {"TERM": "dumb"}])
def test_no_color_and_dumb_terminals_get_no_escape_codes(
    shell: str, setting: dict[str, str]
):
    output = run_in_terminal(
        shell, 'ok "done"; warn "careful"; error "broken"; step "Step 1"', **setting
    )
    assert "\x1b" not in output
    assert "ok: done" in output


@pytest.mark.parametrize("shell", shells())
def test_the_bullet_falls_back_to_a_hyphen_outside_utf8(shell: str):
    assert run(shell, 'bullet "one"').startswith("  · one")
    assert run(shell, 'bullet "one"', LC_ALL="C", LANG="C").startswith("  - one")


@pytest.mark.parametrize("shell", shells())
def test_a_long_question_wraps_and_keeps_its_last_line_for_the_answer(shell: str):
    output = run(
        shell, f'prompt_line "{LONG} [Y/n]:"; printf "%s|" "$prompt"', COLUMNS="60"
    )
    # Without a terminal for the answer, read -p shows nothing, so neither does
    # prompt_line: only the last line is left for read -p.
    assert output.endswith("[Y/n]: |")
    assert output.startswith("  ") and len(output.rstrip("|")) <= 60


@pytest.mark.parametrize("shell", shells())
def test_how_enter_and_ctrl_c_work_is_said_once(shell: str):
    hint = "Press Enter to keep the value in [brackets]. Ctrl-C cancels"
    body = 'prompt_line "First? [Y/n]:"; prompt_line "Second? [Y/n]:"'
    typed = run_typing(shell, body, GROUNDLINE_ENTER_HINT="")
    assert typed.count(hint) == 1, typed
    # Nobody is typing, so there's nobody to tell.
    assert hint not in run(shell, body)


@pytest.mark.parametrize("shell", shells())
def test_an_empty_message_prints_its_prefix_under_set_u(shell: str):
    # bash 3.2 calls an empty array unbound under set -u, which every script sets.
    output = run(shell, 'set -u; ok ""; check_ok "   "; echo end')
    assert output == "ok: \n  ok: \nend\n"


def test_checker_details_go_under_their_result():
    output = run(
        "bash",
        "check_results <<<$'HTTPS works with a trusted certificate"
        "\\n  Certificate expires: Nov 25, 2026'",
    )
    assert printed_lines(output) == [
        "  ok: HTTPS works with a trusted certificate",
        "      Certificate expires: Nov 25, 2026",
    ]


def test_no_message_is_wrapped_by_hand():
    """A message continued on its own echo line breaks wrapping: pass one string."""
    offenders: list[str] = []
    for path in sorted(SCRIPTS.glob("aws-*")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if re.match(r'\s*echo "    \S', line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, "\n".join(offenders)
