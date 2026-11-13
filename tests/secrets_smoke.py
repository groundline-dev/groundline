"""Run the real pinned Gitleaks against disposable repositories; stdlib only.

Usage: python3 tests/secrets_smoke.py (gitleaks must be on PATH).
CI runs this before auditing history. All credential-shaped text is synthetic
and stays in temporary local repositories.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "check_secrets.py"


class SecretChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="groundline-secrets-test-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.git("init", "-q")
        shutil.copyfile(ROOT / ".gitleaks.toml", self.repo / ".gitleaks.toml")
        self.git("add", ".")
        self.git("commit", "-qm", "chore: initialize fixture")

    def git(self, *args: str) -> str:
        return subprocess.check_output(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=Secret check test",
                "-c",
                "user.email=test@example.invalid",
                *args,
            ],
            cwd=self.repo,
            text=True,
        ).strip()

    def scan(self, mode: str, expected: int) -> str:
        result = subprocess.run(
            [sys.executable, str(CHECK), mode],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_empty_file_rename_and_deleted_history(self) -> None:
        (self.repo / "prod.env").touch()
        self.git("add", "prod.env")
        self.scan("staged", 1)
        self.git("commit", "-qm", "test: empty environment file")
        self.git("rm", "prod.env")
        self.scan("staged", 0)
        self.git("commit", "-qm", "test: remove environment file")
        self.scan("history", 1)
        (self.repo / "safe.txt").write_text("ordinary placeholder\n")
        self.git("add", "safe.txt")
        self.git("commit", "-qm", "test: sample")
        self.git("mv", "safe.txt", ".env-renamed")
        self.scan("staged", 1)

    def test_simple_password_is_blocked_by_filename(self) -> None:
        (self.repo / ".env-old").write_text("POSTGRES_PASSWORD=ordinary-example\n")
        self.git("add", ".env-old")
        output = self.scan("staged", 1)
        self.assertIn("env-file", output)
        self.scan("tracked", 1)

    def test_example_contents_are_scanned_and_output_is_redacted(self) -> None:
        example = self.repo / ".env.example"
        example.write_text("GITHUB_TOKEN=\n")
        self.git("add", ".env.example")
        self.scan("staged", 0)
        # Construct a synthetic provider-pattern value; never publish this fixture.
        synthetic = "ghp_" + "Ab3De6Gh9Jk2Mn5Pq8St1Vw4Yz7Bc0Ef3Hi6"
        example.write_text(f"GITHUB_TOKEN={synthetic}\n")
        self.scan("staged", 0)  # The secret-shaped change is not staged yet.
        output = self.scan("tracked", 1)
        self.assertNotIn(synthetic, output)
        self.git("add", ".env.example")
        output = self.scan("staged", 1)
        self.assertNotIn(synthetic, output)
        self.git("commit", "-qm", "test: synthetic secret")
        self.git("rm", ".env.example")
        self.git("commit", "-qm", "test: remove synthetic secret")
        output = self.scan("history", 1)
        self.assertNotIn(synthetic, output)

    def test_secret_introduced_only_by_merge_resolution_is_detected(self) -> None:
        original = self.git("branch", "--show-current")
        self.git("checkout", "-qb", "topic")
        (self.repo / "topic.txt").write_text("topic\n")
        self.git("add", "topic.txt")
        self.git("commit", "-qm", "test: topic change")
        self.git("checkout", "-q", original)
        (self.repo / "main.txt").write_text("main\n")
        self.git("add", "main.txt")
        self.git("commit", "-qm", "test: main change")
        self.git("merge", "--no-commit", "--no-ff", "topic")
        synthetic = "ghp_" + "Ab3De6Gh9Jk2Mn5Pq8St1Vw4Yz7Bc0Ef3Hi6"
        (self.repo / "resolution.txt").write_text(f"GITHUB_TOKEN={synthetic}\n")
        self.git("add", "resolution.txt")
        self.git("commit", "-qm", "test: merge-only synthetic secret")
        self.scan("history", 1)


if __name__ == "__main__":
    unittest.main()
