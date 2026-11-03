# Domain wording lives in profiles; the code says source and summary

The first domain is clinical, and the obvious names in code would be transcript and note. We kept the code, data models and CLI domain-neutral, saying source and summary, and put everything domain-specific into a domain profile: the prompts, the severity rubric, and which placeholders in the prompts mean the source and the summary. A profile is kept separate from a pipeline spec: the profile says what matters in a domain, and the spec says how to check. This follows the Oct 23 proposal, where groundline is a general evaluator for a summary and its source and the extraction guide and rubric live in configuration rather than code. Finance is a likely second domain, and adding it should mean writing a profile, not changing code.

## Consequences

- The clinical prompts and rubric are published with the open-source repo. This is intended.
- The clinical prompts and rubric are copied word for word from the pipeline they were measured on. Editing them means the measured results no longer apply to the validated preset (see ADR 0004).
