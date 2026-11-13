# Repository secret audit

## 2026-11-13

Scanned a fresh mirror of `https://github.com/groundline-dev/groundline.git` with Gitleaks v8.30.1, using its built-in rules plus the repository's `env-file` rule. The release tag resolves to `83d9cd684c87d95d656c1458ef04895a7f1cbd8e`. The downloaded scanner's SHA-256 was verified against the release asset metadata before execution.

The mirror contained 40 advertised refs (8 branch refs and 32 pull-request refs) and 290 reachable commits. No tag refs were advertised. Its default-branch HEAD was `5f32c9b66cbf529fa42db9faf6a8ea46ac0cdebf`. The content scan used `--log-opts="--all --full-history -m"` to include merge-resolution changes; an additional filename scan checked additions and changes across those commits, including empty files and renames.

The content scan reported one false positive in `scripts/aws-smoke-test` at commit `8aad8f879f00c4f52021ea8a717c90c77500d43c`: the authentication rejection test deliberately supplies an invalid value and expects HTTP 401. The value does not match groundline's API-key format. Only that finding's fingerprint is exempted in `.gitleaksignore`.

After that exception, the content scan and the filename scan passed with **zero unresolved findings**. No real credentials were identified, so no credential rotations or history changes were needed as a result of this scan. Reports were redacted and kept outside tracked files; no credential values are recorded here.

This is evidence about the refs available at the time of the audit, not a guarantee that no secret has ever been exposed. Deleted unreachable history, unavailable fork refs, issue comments, workflow logs and external copies were not covered. Gitleaks cannot recognize every secret. See [Secret checks](../DEVELOPMENT.md#secret-checks) for the ongoing checks and exposure response.

### GitHub detection settings

The repository security settings were inspected during this audit. Secret Protection showed partner-pattern scanning and supported-secret push protection, but no Generic patterns or Non-provider patterns control. That additional setting remains unverified and unchanged; an organization administrator needs to confirm eligibility and enable it if available. The local and CI checks do not depend on that setting.
