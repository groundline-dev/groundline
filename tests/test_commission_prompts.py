"""The commission prompts are the ones omission-pipeline measured, unchanged.

The parity test replays the quick check's prompt, but the refute prompt is off in
the preset, so nothing else would notice it change. These hashes are of the
prompt files in omission-pipeline (commit 567908c), prompts/quick_check_v1.txt and
prompts/support_refute_v1.txt.

If you change a prompt on purpose, the measured results no longer apply to the
`commission_quick` preset (see ADR 0002 and 0004): update the hash, and say so in
the README.
"""

import hashlib
from pathlib import Path

import pytest

from groundline.profiles import load_builtin_profile

PROMPT_HASHES = {
    "quick_check_v1": (
        "1e6362227b84c641997441937ee1f6514f400ed83d8045678a867b9eb69e1f2e"
    ),
    "support_refute_v1": (
        "02954f2ac0e5f646cbb1149f908d7347aaee0a2906ccbbc450f0d9fdadfed0bf"
    ),
}


@pytest.mark.parametrize("name", PROMPT_HASHES)
def test_the_prompt_is_unchanged_from_omission_pipeline(name: str):
    text = load_builtin_profile("clinical").texts[name]

    assert hashlib.sha256(text.encode()).hexdigest() == PROMPT_HASHES[name], (
        f"the clinical profile's '{name}' prompt has changed since omission-pipeline "
        "measured it, so the commission_quick results no longer apply to it"
    )


def test_the_prompt_files_are_read_byte_for_byte():
    # The profile loader reads text, so check the files themselves too: a stray
    # carriage return or trailing change would alter the prompt that is sent.
    folder = (
        Path(__file__).parent.parent
        / "src"
        / "groundline"
        / "profiles"
        / "clinical"
        / "prompts"
    )
    for name, expected in PROMPT_HASHES.items():
        data = (folder / f"{name}.txt").read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected
