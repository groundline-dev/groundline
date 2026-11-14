# The commission check also offers the lean preset

Callers can choose `commission_reverse_lean`, omission-pipeline's all-Opus reverse pipeline with a shorter support check, instead of the default `commission_quick` (ADR 0007). On 172 commission pairs it caught 166 and flagged 0 of 110 clean notes, matching the exact preset and catching 15 more than quick, at about $0.28 per note against quick's $0.002. It isn't confirmed to its pre-registered standard, since it missed the paired-score margin by less than two pairs on pairs that had already informed the choice of setting, but nothing measured caught more, so we offer it and say it isn't confirmed wherever the preset is described.

## Consequences

- If a fresh test on new commission pairs comes out worse, callers who depend on the preset will have to move.
- Its one threshold level, `default`, is 0.80, calibrated for this pipeline and not taken from quick (ADR 0004).
