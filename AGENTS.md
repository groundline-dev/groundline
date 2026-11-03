# groundline

## Commits

- Write commit messages in the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) format: `<type>(<optional scope>): <description>`, using types such as `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `build`, `ci`, `perf` and `style`. Mark breaking changes with `!` after the type or a `BREAKING CHANGE:` footer.
- Don't add `Co-authored-by` lines yourself; the `.githooks/commit-msg` hook adds the team's.

## Domain language and decisions

Before changing the design, read `CONTEXT.md` and `docs/adr/`, and raise it before going against a recorded decision.

The rules below apply only when discussing or changing the design. In other work (bug fixes, reviews, refactors), don't edit `CONTEXT.md` or `docs/adr/` and don't stop to ask about wording; mention a suggested addition at the end instead.

### Glossary (`CONTEXT.md`, repo root)
- When a domain term is resolved in a design discussion, add it to `CONTEXT.md` immediately; don't batch.
- Only project-specific domain terms. No general programming concepts.
- Format: `**Term**:` then a 1-2 sentence definition of what it is, then `_Avoid_: synonym, synonym`, optionally followed by a bracketed reason. Pick one word per concept.
- No implementation details or specs in `CONTEXT.md`.
- In a design discussion, if a user's wording conflicts with the glossary or is vague, flag it and ask which term they mean.

### Decisions (`docs/adr/`)
- Offer to write an ADR only when the decision is (1) hard to reverse, (2) surprising without context, and (3) a real trade-off between alternatives. Otherwise skip it.
- Files: `docs/adr/NNNN-slug.md`, next number after the highest existing. Create the folder when the first ADR is needed.
- Format: a title plus 1-3 sentences (context, decision, why). Add Status, Considered Options or Consequences only when they add value.
