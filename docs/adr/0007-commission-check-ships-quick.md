# The commission check ships the quick variant

The commission pipeline groundline was ported from has three presets: an exact one (claims, support check and refute, all Opus), a lean one, and a quick one, which is a single Claude Haiku 5.5 call that lists only the summary's unsupported or contradicted statements. On 172 commission pairs the quick preset caught 151 with 1/110 clean notes flagged, at about $0.002 per note, against 166 caught at $0.28 for lean and $0.40 for exact. The team chose quick for similar results at a far lower cost, and it is the only one of the three that was confirmed against its pre-registered standard.

## Consequences

- It catches fewer commissions than the other two (151 against 166 of 172).
- It uses Haiku, not Opus. Anthropic's BAA is understood to cover services and features rather than specific models, so Haiku should be covered, but confirm that before production (see ADR 0006).
- The exact and lean presets, and the quick check's opt-in `repeats` and `kinds` settings, are left out for now.
