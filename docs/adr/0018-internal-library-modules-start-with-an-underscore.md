# Internal library modules start with an underscore

Callers import groundline's public names from `groundline_core` itself, as its `__all__` lists them. The library's internal modules (`_core`, `_evaluators`, `_clients`, `_evaluate`, `_result`, `_findings` and `_settings`) start with an underscore, as PEP 8 recommends and httpx and the Anthropic SDK do, so an import from one shows that it reaches inside; no tool enforces this, and pyright strict doesn't flag such imports. The server and CLI modules (`app`, `api`, `db`, `cli`, `command` and the rest) and the `presets` and `profiles` folders keep plain names, because deployments run them by path, such as `groundline_core.app:app`, and users' specs link to the published `presets/pipeline.schema.json`.

## Consequences

- A new library module gets an underscore too. Anything callers need from it is re-exported from `groundline_core` and added to `__all__` and the guard test in `tests/test_public_interface.py`.
