# The commission check also offers the lean preset

Callers can choose `commission_reverse_lean`, omission-pipeline's all-Opus reverse pipeline with a shorter support check, instead of the default `commission_quick` (ADR 0007). On 172 commission pairs it caught 166 and flagged 0 of 110 clean notes, matching the exact preset and catching 15 more than quick, at about $0.28 per note against quick's $0.002. It failed both of its pre-registered tests, so it isn't confirmed to that standard. We offer it because nothing measured caught more, and every description of the preset says it isn't confirmed.

On detection, the lower end of the paired-score confidence interval against the exact preset (-0.0145) fell below the -0.01 margin, a margin less than two pairs wide. Only 3 pairs changed outcome, a net loss of half a pair. These pairs weren't fresh: earlier tests had used them, and one of those tests led to trying this setting.

On false alarms, across 108 ACI-Bench and PriMock57 notes with nothing added, it flagged 39 against a limit of 32 (the exact preset flagged 31). A later blind review found most flagged statements really weren't said in the consultation, so the extra flags look like real catches, but the pre-registered verdict stands.

## Consequences

- No fresh commission pairs exist yet: all 202 in OmissionBench are used. If a test on new pairs comes out worse, callers who depend on the preset will have to move.
- Its one threshold level, `default`, is 0.80: the exact preset's level, at which lean was tested (ADR 0004). Lean scores borderline claims higher than the exact preset, so it may need its own threshold, which would have to be calibrated and tested.
