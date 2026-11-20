"""Every code example in the README runs, against the fake server.

A reader copies these examples, so a change that breaks one has to fail a test.
Each Python block is run as written in its own `python -I` process, with the URL
and the API key in the process's environment, the way the guide tells a reader
to set them. A block runs unless the line right above its opening fence is a
comment saying otherwise:

    <!-- not run: why -->   the block is shown, not run (an install command,
                            a shell command, a fragment that needs a real server)
    <!-- fake: 429 -->      the fake server answers every check with a 429

Both comments are invisible when the README is rendered. Shell blocks must say
why they aren't run, so a new example can't be left out of the tests unnoticed.
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import API_KEY_VARIABLE, URL_VARIABLE, FakeServer
from groundline_client.client import DEFAULT_MAX_RETRY_SECONDS

README = Path(__file__).resolve().parents[1] / "README.md"
CHECK_PATHS = ["/v1/omissions", "/v1/commissions"]
RETRY_AFTER_SECONDS = 200
"""Longer than a call's whole time limit, so the client stops at once and raises
the error instead of waiting before a retry."""
assert RETRY_AFTER_SECONDS > DEFAULT_MAX_RETRY_SECONDS, (
    "the 429 example only fails fast while Retry-After is longer than the client's "
    "default retry window; raise RETRY_AFTER_SECONDS"
)

MARKER = re.compile(r"^<!-- (?P<note>not run: .+|fake: 429) -->$")


@dataclass(frozen=True)
class Block:
    line: int
    """Where the opening fence is, counting from 1."""
    heading: str
    language: str
    code: str
    note: str | None
    """What the marker above the fence says, if there is one."""

    @property
    def label(self) -> str:
        first = next((row for row in self.code.splitlines() if row.strip()), "")
        return f"README line {self.line} ({self.heading!r}), starting {first!r}"


def read_blocks(text: str) -> tuple[list[Block], list[int]]:
    """The fenced blocks, and the lines of markers that sit above no block."""
    lines = text.splitlines()
    blocks: list[Block] = []
    used: set[int] = set()
    heading = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
        elif line.startswith("```"):
            end = index + 1
            while end < len(lines) and not lines[end].startswith("```"):
                end += 1
            if end == len(lines):
                raise ValueError(
                    f"the code block opened on line {index + 1} never closes"
                )
            above = MARKER.match(lines[index - 1]) if index else None
            if above:
                used.add(index - 1)
            blocks.append(
                Block(
                    line=index + 1,
                    heading=heading,
                    language=line[3:].strip(),
                    code="\n".join(lines[index + 1 : end]) + "\n",
                    note=above["note"] if above else None,
                )
            )
            index = end
        index += 1
    stray = [
        number + 1
        for number, line in enumerate(lines)
        if line.startswith("<!--") and number not in used
    ]
    return blocks, stray


def unmarked_non_python_blocks(blocks: list[Block]) -> list[str]:
    return [
        block.label
        for block in blocks
        if block.language != "python" and not (block.note or "").startswith("not run")
    ]


BLOCKS, STRAY_COMMENTS = read_blocks(README.read_text())
PYTHON_BLOCKS = [block for block in BLOCKS if block.language == "python"]
RUN_BLOCKS = [
    block
    for block in PYTHON_BLOCKS
    if block.note is None or not block.note.startswith("not run")
]


def test_every_comment_in_the_readme_is_a_known_marker_above_a_code_block():
    assert STRAY_COMMENTS == [], (
        f"README lines {STRAY_COMMENTS}: a comment that isn't `not run: why` or "
        "`fake: 429`, or that isn't on the line right above a code block"
    )


def test_every_block_that_is_not_python_says_why_it_is_not_run():
    assert unmarked_non_python_blocks(BLOCKS) == [], (
        "blocks that aren't python need a `<!-- not run: why -->` line above"
    )


@pytest.mark.parametrize("language", ["sh", "py", "toml", "text", "python3"])
def test_a_block_in_any_language_but_python_is_flagged_until_it_has_a_reason(
    language: str,
):
    text = f"# Title\n\n```{language}\nx = 1\n```\n"
    marked = f"# Title\n\n<!-- not run: shown only -->\n```{language}\nx = 1\n```\n"

    assert len(unmarked_non_python_blocks(read_blocks(text)[0])) == 1
    assert unmarked_non_python_blocks(read_blocks(marked)[0]) == []


def test_a_fence_left_open_names_the_line_it_opens_on():
    text = "# Title\n\n```python\nprint(1)\n"

    with pytest.raises(ValueError, match="line 3"):
        read_blocks(text)


def test_every_block_names_its_language():
    assert [block.label for block in BLOCKS if not block.language] == []


@pytest.mark.parametrize(
    "call",
    [
        "evaluate_omissions",
        "evaluate_omissions_async",
        "evaluate_commissions",
        "evaluate_commissions_async",
        "health",
    ],
)
def test_the_guide_has_a_run_example_that_makes_each_call(call: str):
    pattern = re.compile(rf"\.{call}\(")
    assert any(pattern.search(block.code) for block in RUN_BLOCKS), (
        f"no example that is run calls {call}()"
    )


def test_the_guide_has_a_run_example_that_handles_a_429():
    assert any(
        block.note == "fake: 429" and "RateLimitError" in block.code
        for block in RUN_BLOCKS
    ), "no example that is run catches RateLimitError from a 429 answer"


@pytest.mark.parametrize(
    "block", RUN_BLOCKS, ids=[f"line-{block.line}" for block in RUN_BLOCKS]
)
def test_each_python_example_runs_as_written(
    block: Block, fake_server: FakeServer, api_key: str, tmp_path: Path
):
    rate_limited = block.note == "fake: 429"
    if rate_limited:
        for path in CHECK_PATHS:
            fake_server.reply(
                "POST",
                path,
                429,
                {"detail": "Too many requests."},
                {"Retry-After": str(RETRY_AFTER_SECONDS)},
            )
    script = tmp_path / "example.py"
    script.write_text(block.code)

    try:
        finished = subprocess.run(
            [sys.executable, "-I", str(script)],
            cwd=tmp_path,
            env={
                "PATH": os.environ.get("PATH", ""),
                URL_VARIABLE: fake_server.url,
                API_KEY_VARIABLE: api_key,
                "NO_PROXY": "127.0.0.1",
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{block.label} did not finish in 60 seconds")

    assert finished.returncode == 0, f"{block.label} failed:\n{finished.stderr}"
    assert fake_server.requests, f"{block.label} never called the API"
    assert {request.headers["authorization"] for request in fake_server.requests} == {
        f"Bearer {api_key}"
    }, f"{block.label} didn't send the API key from the environment"
    if rate_limited:
        assert str(RETRY_AFTER_SECONDS) in finished.stdout, (
            f"{block.label} didn't print the Retry-After wait from the 429"
        )
