---
name: maintain-api-docs
description: Keep groundline's /docs and /api pages (OpenAPI descriptions, examples, error statuses) in step with the API. Use after adding or changing a route, request/response field, status code, auth behavior, or default preset, or when asked to audit API docs.
---

# Maintain groundline's API docs

`/docs` and `/api` render one OpenAPI schema. The code is the source of truth; this skill is how to keep it accurate and consistent. Don't change API behavior to make an example pass. If behavior contradicts an ADR or the intended contract is unclear, report it instead of choosing.

If a file, symbol or test named here no longer exists, find what replaced it (grep, `git log -S`) and update this skill in the same change. If a row in the "adds over FastAPI" table no longer applies, ask before removing it — that may be a regression, not drift.

## What groundline adds over stock FastAPI (keep all of it working)

| Addition | Where | Test |
|---|---|---|
| `/api` serves the `/docs` page, honoring `root_path` | `app.py` `api_docs` | `test_api_and_docs_are_the_same_page_behind_a_proxy` |
| Release version vs `/v1` API version; `v1` tag | `app.py` `DESCRIPTION` | `test_the_docs_show_groundlines_release_and_label_the_api_version` |
| Labeled example for every error status | `api/common.py` `_error` | `test_each_check_has_a_valid_example_for_every_error` |
| Own 422 body, never echoing input | `FAILURE_RESPONSES`, `api/errors.py` | `test_a_real_422_has_the_documented_shape` |
| 401 with `WWW-Authenticate: Bearer` | `PROTECTED_RESPONSES` | `test_a_401_documents_its_bearer_challenge` |
| 502/503 examples derived from the route's pipeline | `check_responses` | the 502/503 tests |
| Success examples keep their nulls (FastAPI strips them from the schema it builds) | `app.py` `_openapi`, `_RESPONSE_MODELS` | `test_the_served_examples_keep_the_nulls_a_real_response_has` |
| Request and success examples validate | `CheckRequest`, response models | `test_each_check_has_a_valid_example_request_and_response` (the docs' own copy must validate) |

## House style (match the existing text)

- Field descriptions are attribute docstrings: what the value means, plus range/unit, e.g. "From 0 to 1: how sure…".
- Error descriptions say when the status happens, in one or two sentences, plus what the caller can do if useful.
- Examples: go through `_error` with a picker label; use the invented consultation; write a null where the response has one (a commission's `summary_evidence`), because `app.py` puts each response model's own examples back after FastAPI strips them. Never echo rejected input.
- Derive stage names, models, and costs from presets; don't hard-code them.
- Intro keeps three parts: purpose + "not for clinical use", Authentication, Versions (until the API is public, v1 may still break a caller; once it is public, v1 only adds).

## When X changes, update Y

| Change | Update |
|---|---|
| New check route | `responses=check_responses(...)`, `tags`, request/response examples, its response model in `_RESPONSE_MODELS` (`app.py`), add to `tests/auth_helpers.ENDPOINTS`, README "Using the API" |
| New/renamed field | Docstring, success example, README if shown; renaming or removing a field breaks callers: allowed in `/v1` until the API is public, a `/v2` change after; flag it either way |
| New status code or error message | `_error` entry with example; real-response test if the shape is new |
| Auth change | `DESCRIPTION` Authentication, 401/503 entries, README |
| Default preset / threshold change | Threshold docstrings, success example arithmetic (derive from code) |

## Verify

Run `tests/test_api_docs.py` and the affected API/auth tests, plus lint and type checks (use `.venv/bin/` if the installed uv is outside the required range; don't change the range). Use fake auth and model dependencies — no database, no Anthropic spend. For presentation changes, open `/docs` and `/api` and check labels and example pickers; say so if you couldn't. Report what drifted, what you verified, and what's still uncertain — a passing schema test doesn't mean the prose is current.
