# PR 25 review follow-up

## 2026-11-11 — Review implementation

**Decisions:** The user authorized ADR 0014 for IP-based HTTPS; YY and Nathalia previously supported the endpoint change. Public-by-default approval remains pending with Shannon/the team. No second ADR is created. Preserve the restricted test-deployment definition and use disposable deployment for the broader category.

**Built:** ADR, migration instructions, operator-guide cleanup, glossary correction, shared output formatting, parallel port checks, required HTTP tester-address validation, removal of the keep-path fetch, shared deadline path, Caddy-specific forwarded-header trust, HTTPS CI input filtering, and a shared Python image pin.

**Traps:** macOS Bash 3 treats empty arrays as unbound under `set -u`; expand optional arguments with the compatible guarded form. Terraform tests inherit local tfvars; explicitly set their baseline access mode so local HTTPS settings cannot change HTTP assertions. Tool caches and provider/test sockets require the execution environment's sandbox escalation. Docker Desktop was initially stopped and was started for local checks.

**Review dispositions:** Keep rendered Caddy configuration: Compose environment variables are fixed when a container is created, so a simple env_file replacement breaks IP refresh on restart. Keep independent production/test port lists and successful all-true evidence: these are intentional verification boundaries, not defects. Keep configured-vs-deployed access-mode checks and bounded-vs-normal SSM semantics distinct. Broad test-runner and SSM refactoring is not needed to fix this PR's behavior; avoid coupling test-specific setups merely because their subprocess plumbing looks alike. The lifecycle test's slow response and CA-outage phases remain sequential to retain independent failure attribution; CI now skips unrelated changes.

**Validation:** 27 mock Terraform tests passed. Focused access-mode and TLS tests passed after the Bash fix. Full checks and local lifecycle checks are being rerun; see the final entry for results. No AWS resources were created.

**PR text to apply when publishing:** In the behavior checklist use `make aws-https-smoke RESTART=caddy` and `make aws-https-smoke RESTART=host` in full. Link ADR 0014 and explain that restart recovery, boot-time address discovery and certificate lifecycle CI validate the HTTPS feature rather than implementing lasting deployment. Do not claim team approval for unrestricted caller IPs; that remains decision #2. No GitHub comments or PR metadata were posted during this implementation.

**Final validation:** `make check` passed (lint, formatting, pyright; 760 tests passed, 40 deselected). `make infra-check` passed (27 mock tests). `make https-smoke` passed real local issuance, trust/identity rejection, forwarded-client-IP spoof rejection, restart retention, automatic renewal, the 125-second response, expiry during CA outage and recovery. Docker test resources were cleaned by the harness. `git diff --check` passed. No AWS deployment, commit, push or review reply was made. Public-access policy approval and PR-description publishing remain external follow-up items.
