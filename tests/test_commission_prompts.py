"""The commission prompts are the ones omission-pipeline measured, unchanged.

The parity tests replay the prompts each preset sends, but the quick preset's
refute is off, so these hashes also hold every prompt still to the files
omission-pipeline measured. quick_check_v1 and support_refute_v1 are hashed from
omission-pipeline commit 567908c; note_claims_v1 and support_check_lean_v1 from
commit e8a4008, the last to change them.

If you change a prompt on purpose, the measured results no longer apply to the
preset that uses it, `commission_quick` or `commission_reverse_lean` (see ADR 0002
and 0004): update the hash, and say so in the README.
"""

import hashlib
from pathlib import Path

import pytest

from groundline_core.profiles import load_builtin_profile

PROMPT_HASHES = {
    "quick_check_v1": (
        "1e6362227b84c641997441937ee1f6514f400ed83d8045678a867b9eb69e1f2e"
    ),
    "support_refute_v1": (
        "02954f2ac0e5f646cbb1149f908d7347aaee0a2906ccbbc450f0d9fdadfed0bf"
    ),
    "note_claims_v1": (
        "1ca35d07a22d9112a63da47a63a05c628e7e4631cbf564ad77086db92149d2b4"
    ),
    "support_check_lean_v1": (
        "b17264afe344752d0bf8741fb5171f52d5d03e793242709a02762a7c614bb2bc"
    ),
}


@pytest.mark.parametrize("name", PROMPT_HASHES)
def test_the_prompt_is_unchanged_from_omission_pipeline(name: str):
    text = load_builtin_profile("clinical").texts[name]

    assert hashlib.sha256(text.encode()).hexdigest() == PROMPT_HASHES[name], (
        f"the clinical profile's '{name}' prompt has changed since omission-pipeline "
        "measured it, so the measured results no longer apply to its preset"
    )


def test_the_prompt_files_are_read_byte_for_byte():
    # The profile loader reads text, so check the files themselves too: a stray
    # carriage return or trailing change would alter the prompt that is sent.
    folder = (
        Path(__file__).parent.parent
        / "src"
        / "groundline_core"
        / "profiles"
        / "clinical"
        / "prompts"
    )
    for name, expected in PROMPT_HASHES.items():
        data = (folder / f"{name}.txt").read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected
