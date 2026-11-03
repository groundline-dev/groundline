# Attribution

`source.txt` (the consultation transcript) and `summary.txt` (the clinical note
with one fact removed) are row `primock|day2_consultation05|omit` of
**OmissionBench**, version `factorial-v1`: Fox, S., Markham, L., Lail, R. and
Karotsieris, M. (2026), Hugging Face `ComposoAI/OmissionBench`, licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Companion paper:
*LLM Judges Verify Presence, Not Absence: Omission Blindness in AI Clinical Notes
and What Recovers It*, arXiv:2608.31016.

OmissionBench requires this upstream attribution to travel with the data:

> PriMock57, Babylon Health, licensed under CC BY 4.0,
> github.com/babylonhealth/primock57. Changes made: transcripts normalised; the
> clinician reference notes were audited against their own transcripts and
> repaired; single-error variants were generated from the repaired notes.

`recorded.json` holds the prompts sent and replies received when the all-Opus
omission pipeline (extract, rank, batched check, refute) ran on this row, with
the facts, verdicts and score it produced. groundline's parity test replays those
replies and checks it sends identical prompts and reaches identical results.

Not for clinical use. No real patients are in the data.
