# The commission check also offers the lean preset

Callers can choose `commission_reverse_lean`, omission-pipeline's all-Opus reverse pipeline with a shorter support check, instead of the default `commission_quick` (ADR 0007). On 172 commission pairs it caught 166 and flagged 0 of 110 clean notes, 15 more catches than quick, at about $0.14 per note against $0.001, but it failed both of its pre-registered tests: a paired-score margin less than two pairs wide, on pairs earlier tests had used, and a false-alarm limit on 108 notes from other sources (39 flagged against a limit of 32). We offer it because nothing measured caught more, and every description of it says it isn't confirmed to that standard.

## Consequences

- A later blind review found that most of the 39 flagged statements really weren't said in the consultation, including all 9 that only lean flagged. They look like real catches, but the pre-registered verdict stands.
- No fresh commission pairs exist yet: all 202 in OmissionBench are used. If a test on new pairs comes out worse, callers who depend on the preset will have to move.
- Its one threshold level, `default`, is 0.80: the exact preset's level, at which lean was tested (ADR 0004). Lean scores borderline claims higher than the exact preset, so it may need its own threshold, which would have to be calibrated and tested.
