"""Every code example in the library guide and the README runs, against a fake
of Anthropic's API.

A reader copies these examples, so a change that breaks one has to fail a test.
Each Python block is run as written in its own `python -I` process, with
ANTHROPIC_BASE_URL pointing at a local fake of Anthropic's Messages API and a
made-up ANTHROPIC_API_KEY, so nothing is paid for and nothing leaves the
machine. The examples read transcript.txt and note.txt, which the test fills
with the sample consultation in tests/omission_helpers.py, and the fake answers
each step with replies that quote it.

Each way of giving the check an Anthropic API key gets its own made-up
Anthropic API key, so a block passes only if the one it names is the one sent:
ANTHROPIC_API_KEY in the environment, the anthropic_api_key argument (read from
MY_APP_ANTHROPIC_API_KEY, as the guide's example does), or
config/groundline.env, the env_file the guide names. The environment outranks
env_file, so the env_file example runs without ANTHROPIC_API_KEY.
(test_evaluate_anthropic_key.py checks that an unnamed ./.env is never read.)

In the guide, a block that isn't Python must have a comment on the line right
above its opening fence saying why it isn't run, so a new example can't be left
out of the tests unnoticed:

    <!-- not run: why -->

The comment is invisible when the guide is rendered. The README's other blocks
(the command line, the API, YAML) aren't checked for one.
"""

import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import groundline_core
from tests.anthropic_helpers import message
from tests.commissions_helpers import issue, issues_reply
from tests.markdown_helpers import (
    README,
    REPOSITORY,
    ROOT,
    broken_links,
    fences,
    heading_ids,
    link_targets,
)
from tests.omission_helpers import SOURCE, SUMMARY, Script, not_flagged_script

GUIDE = ROOT / "docs" / "library.md"
GUIDE_URL = f"{REPOSITORY}/blob/main/docs/library.md"
ENVIRONMENT_ANTHROPIC_API_KEY = "sk-ant-fake-key-in-the-environment"
ARGUMENT_ANTHROPIC_API_KEY = "sk-ant-fake-key-passed-as-an-argument"
ENV_FILE_ANTHROPIC_API_KEY = "sk-ant-fake-key-in-an-env-file"
PYTHON = {"python", "py", "python3"}
"""The languages that make a block a Python block, as the first word of its
fence's info string, in any case."""
NOT_RUN = re.compile(r"<!-- not run: \S.* -->")
QUICK_COMMISSIONS_REPLY = issues_reply(
    # The transcript says the finger "bent back", never which one.
    issue("hyperextended her right index finger", "unsupported", 70, "supporting")
)
"""The quick commission check's one reply, quoting the sample note."""


# --- reading the code blocks ---------------------------------------------------


@dataclass(frozen=True)
class Block:
    path: Path
    line: int
    """The line of the opening fence, counted from 1."""
    language: str
    code: str
    marked_not_run: bool
    """Whether the line right above the fence says why the block isn't run."""

    @property
    def label(self) -> str:
        return f"{self.path.relative_to(ROOT)}:{self.line}"


def read_blocks(path: Path, text: str) -> list[Block]:
    """The fenced code blocks in Markdown `text`, in order."""
    lines = text.splitlines()
    blocks: list[Block] = []
    for opened, closed, info in fences(lines):
        above = lines[opened - 1].strip() if opened > 0 else ""
        blocks.append(
            Block(
                path=path,
                line=opened + 1,
                language=info.split()[0].lower() if info else "",
                code="\n".join(lines[opened + 1 : closed]) + "\n",
                marked_not_run=bool(NOT_RUN.fullmatch(above)),
            )
        )
    return blocks


def guide_blocks() -> list[Block]:
    return read_blocks(GUIDE, GUIDE.read_text())


def python_blocks() -> list[Block]:
    """The Python blocks the test runs, from the README and the guide."""
    blocks = read_blocks(README, README.read_text()) + guide_blocks()
    return [b for b in blocks if b.language in PYTHON and not b.marked_not_run]


def guide_python_code() -> list[str]:
    return [b.code for b in python_blocks() if b.path == GUIDE]


def section(text: str, heading: str) -> str:
    """The text under `## heading`, up to the next `## ` heading."""
    match = re.search(
        rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    assert match, f"no '## {heading}' section"
    return match.group(1)


# --- a fake of Anthropic's Messages API -----------------------------------------


def reply_for(prompt: str, omission_script: Script) -> str:
    """The scripted reply for one step of the default omission or commission
    check. The quick commission check's prompt is the only one with both the
    transcript and the note. The omission steps get the replies of a full run
    that doesn't flag the sample, told apart as the omission tests do.
    (commissions_helpers.step_of can't help here: it would also claim the
    omission check's extract and rank prompts, which hold the transcript.)"""
    if "<note>" in prompt and "<transcript>" in prompt:
        return QUICK_COMMISSIONS_REPLY
    return omission_script(prompt)


@dataclass
class FakeAnthropic:
    url: str
    anthropic_api_keys: list[str]
    """The Anthropic API key each request carried, in order. The Anthropic SDK
    sends it as a Bearer token."""
    problems: list[str]
    """What the fake couldn't answer, so a test fails with the reason rather
    than waiting while the check retries."""


@pytest.fixture
def fake_anthropic() -> Iterator[FakeAnthropic]:
    anthropic_api_keys: list[str] = []
    problems: list[str] = []
    omission_script = not_flagged_script()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            anthropic_api_keys.append(
                self.headers.get("authorization", "").removeprefix("Bearer ")
            )
            try:
                if self.path.split("?")[0] != "/v1/messages":
                    raise ValueError("only the Messages API is faked")
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                prompt = body["messages"][0]["content"]
                if not isinstance(prompt, str):
                    prompt = "".join(part["text"] for part in prompt)
                reply = reply_for(prompt, omission_script)
                answer = message(content=[{"type": "text", "text": reply}])
                status = 200
            except Exception as error:  # a 400, which the check doesn't retry
                problems.append(f"{self.path}: {error!r}")
                answer = {
                    "type": "error",
                    "error": {"type": "invalid_request_error", "message": repr(error)},
                }
                status = 400
            data = json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            pass  # keep the test output quiet

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield FakeAnthropic(
            f"http://127.0.0.1:{server.server_port}", anthropic_api_keys, problems
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# --- the examples run -----------------------------------------------------------


@pytest.mark.parametrize("block", python_blocks(), ids=lambda b: b.label)
def test_each_python_example_runs_as_written(
    block: Block, fake_anthropic: FakeAnthropic, tmp_path: Path
):
    # The folder the example runs in has the files the examples read, and the
    # config/groundline.env the guide's env_file example names.
    (tmp_path / "transcript.txt").write_text(SOURCE)
    (tmp_path / "note.txt").write_text(SUMMARY)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "groundline.env").write_text(
        f"ANTHROPIC_API_KEY={ENV_FILE_ANTHROPIC_API_KEY}\n"
    )
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "ANTHROPIC_BASE_URL": fake_anthropic.url,
        "MY_APP_ANTHROPIC_API_KEY": ARGUMENT_ANTHROPIC_API_KEY,
    }
    if "anthropic_api_key=" in block.code:  # the argument outranks the rest
        environment["ANTHROPIC_API_KEY"] = ENVIRONMENT_ANTHROPIC_API_KEY
        expected_anthropic_api_key = ARGUMENT_ANTHROPIC_API_KEY
    elif "env_file=" in block.code:  # only without the environment, which wins
        expected_anthropic_api_key = ENV_FILE_ANTHROPIC_API_KEY
    else:
        environment["ANTHROPIC_API_KEY"] = ENVIRONMENT_ANTHROPIC_API_KEY
        expected_anthropic_api_key = ENVIRONMENT_ANTHROPIC_API_KEY

    result = subprocess.run(
        [sys.executable, "-I", "-c", block.code],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=environment,
        timeout=60,
    )

    assert fake_anthropic.problems == [], fake_anthropic.problems
    assert result.returncode == 0, f"{block.label} failed:\n{result.stderr}"
    sent = fake_anthropic.anthropic_api_keys
    assert sent, f"{block.label} never called the model"
    assert set(sent) == {expected_anthropic_api_key}, sent


# --- what the guide must cover -------------------------------------------------


def test_the_guide_names_each_install_extra():
    text = section(GUIDE.read_text(), "Install")

    for line in (
        "pip install groundline-core",
        'pip install "groundline-core[cli]"',
        'pip install "groundline-core[server]"',
    ):
        assert line in text, f"the Install section lacks {line!r}"


@pytest.mark.parametrize("check", ["omissions", "commissions"])
@pytest.mark.parametrize("call", ["evaluate_sync(", "await evaluate("])
def test_the_guide_runs_each_check_sync_and_async(check: str, call: str):
    found = [
        code
        for code in guide_python_code()
        if call in code
        and ("DEFAULT_COMMISSIONS_PRESET" in code) == (check == "commissions")
    ]

    assert found, f"no example that is run calls {call} for the {check} check"


@pytest.mark.parametrize("argument", ["anthropic_api_key=", "env_file=", "client="])
def test_the_guide_shows_each_way_to_give_the_check_its_model_access(argument: str):
    assert any(argument in code for code in guide_python_code()), (
        f"no example that is run passes {argument}"
    )


def test_the_comparison_says_who_pays_and_where_the_text_goes():
    text = section(GUIDE.read_text(), "Library, CLI or API")
    rows = [line for line in text.splitlines() if line.startswith("|")]

    assert rows, "the comparison has no table"
    header = rows[0].lower()
    assert "who pays" in header and "where your text goes" in header, rows[0]
    names = [row.strip("|").split("|")[0].strip() for row in rows[2:]]
    assert [n.split(" ")[0] for n in names] == ["Library", "CLI", "API"], names


def test_the_cache_warning_says_it_is_off_by_default_and_keeps_text_on_disk():
    text = section(GUIDE.read_text(), "Caching")

    assert "off by default" in text, text
    assert "on disk" in text, text
    assert "../README.md#caching-and-sensitive-data" in link_targets(text), text


def test_the_public_interface_table_lists_every_public_name():
    text = section(GUIDE.read_text(), "Public interface")
    first_cells = [
        row.strip("|").split("|")[0] for row in text.splitlines() if row.startswith("|")
    ]
    listed = {name for cell in first_cells for name in re.findall(r"`(\w+)`", cell)}

    assert listed == set(groundline_core.__all__), (
        f"in the table only: {sorted(listed - set(groundline_core.__all__))}, "
        f"public but not in the table: {sorted(set(groundline_core.__all__) - listed)}"
    )


def test_each_link_in_the_guide_has_a_file_and_heading_to_go_to():
    def file_for(path: str) -> Path | None:
        if path.startswith(("https://", "http://", "mailto:")):
            return None
        return (GUIDE.parent / path).resolve() if path else GUIDE

    assert broken_links(link_targets(GUIDE.read_text()), file_for) == []


def test_every_guide_block_that_is_not_python_says_why_it_is_not_run():
    unmarked = [
        b.label
        for b in guide_blocks()
        if b.language not in PYTHON and not b.marked_not_run
    ]

    assert unmarked == [], f"no <!-- not run: why --> above: {unmarked}"


def test_the_readme_links_to_the_guide_and_keeps_a_python_example():
    text = README.read_text()

    assert GUIDE_URL in text, "the README doesn't link to the library guide"
    readme_examples = [b for b in python_blocks() if b.path == README]
    assert len(readme_examples) >= 1, "the README has no Python example that is run"


# --- reading the blocks --------------------------------------------------------


def test_a_not_run_reason_applies_only_to_the_block_right_below_it():
    text = (
        "<!-- not run: installs the package -->\n"
        "```sh\npip install groundline-core\n```\n"
        "\n"
        "```sh\nsomething else\n```\n"
        "<!-- not run:  -->\n"
        "```toml\nempty = 'reason'\n```\n"
    )

    blocks = read_blocks(ROOT / "x.md", text)

    assert [(b.language, b.marked_not_run) for b in blocks] == [
        ("sh", True),
        ("sh", False),
        ("toml", False),
    ]


def test_an_unclosed_code_block_names_the_line_it_opened_on():
    with pytest.raises(ValueError, match="opened on line 3 never closes"):
        read_blocks(ROOT / "x.md", "text\n\n```python\nprint('hi')\n")


def test_tilde_and_longer_fences_hold_a_block_up_to_their_own_closing_fence():
    text = (
        "~~~python\nprint('tildes')\n~~~\n"
        "````markdown\n```python\nprint('inside')\n```\n````\n"
    )

    blocks = read_blocks(ROOT / "x.md", text)

    assert [(b.language, b.code) for b in blocks] == [
        ("python", "print('tildes')\n"),
        ("markdown", "```python\nprint('inside')\n```\n"),
    ]


def test_a_blocks_language_is_the_first_word_of_its_info_string_in_any_case():
    text = '```Python title="example.py"\nprint("hi")\n```\n'

    assert [b.language for b in read_blocks(ROOT / "x.md", text)] == ["python"]


def test_a_comment_line_in_a_code_block_is_not_a_heading():
    text = "## Setup\n\n```sh\n# install it first\n```\n"

    assert heading_ids(text) == {"setup"}
