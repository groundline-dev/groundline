# Attribution

`source.txt` (the consultation transcript) and `summary.txt` (the clinical note
with one fact changed) are row `primock|day2_consultation05|change` of
**OmissionBench**, version `factorial-v1`: Fox, S., Markham, L., Lail, R. and
Karotsieris, M. (2026), Hugging Face `ComposoAI/OmissionBench`, licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Companion paper:
*LLM Judges Verify Presence, Not Absence: Omission Blindness in AI Clinical Notes
and What Recovers It*, arXiv:2608.31016.

In this note the hedged impression "presumptive/ working diagnosis TIAs- to be
excluded rather than established" was changed to a firm "confirmed diagnosis of
TIAs". The transcript is the same one the omission fixture uses.

OmissionBench requires this upstream attribution to travel with the data:

> PriMock57, Babylon Health, licensed under CC BY 4.0,
> github.com/babylonhealth/primock57. Changes made: transcripts normalised; the
> clinician reference notes were audited against their own transcripts and
> repaired; single-error variants were generated from the repaired notes.

`recorded.json` holds the prompt sent and the reply received when the quick
commission check (one Claude Haiku 5.5 call) ran on this row. The prompt was
built from omission-pipeline's `prompts/quick_check_v1.txt` by the same
placeholder substitution omission-pipeline uses, not by groundline. groundline's
parity test replays the reply and checks it sends an identical prompt and reaches
the results listed under `expected`.

Not for clinical use. No real patients are in the data.
