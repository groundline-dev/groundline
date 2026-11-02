# groundline

groundline checks an AI-written summary against the source it summarises, and reports what the summary gets wrong. It is domain-neutral: what matters in a domain, such as clinical care, comes from a domain profile.

## Inputs

**Source**:
The original material a summary was written from, such as a consultation transcript.
_Avoid_: transcript, input, document (transcript is clinical wording, kept in the clinical profile)

**Summary**:
The AI-written text being checked against its source, such as a clinical note.
_Avoid_: note, output, generation (note is clinical wording, kept in the clinical profile)

**Domain profile**:
What matters in one domain: its prompts, its severity rubric, and the words its prompts use for the source and the summary.
_Avoid_: config, domain config

**Severity rubric**:
The part of a domain profile that defines how much it matters when a summary leaves out a fact.
_Avoid_: importance guide, ranking guide

## The omission check

**Evaluator**:
One kind of check run on a summary. The omission check is the first.
_Avoid_: metric, scorer

**Fact**:
A single point from the source that a complete summary should record, with a quote from the source as its evidence.
_Avoid_: claim, item, statement

**Omission**:
A fact the summary leaves out or only partly records.
_Avoid_: gap, miss

**Severity**:
How much it would matter if the summary left out a fact, graded by the severity rubric: critical, supporting or peripheral.
_Avoid_: importance, priority

**Verdict**:
Whether the summary records a fact: present, partial or absent.
_Avoid_: label, status, result

**Missing confidence**:
How sure the check is that a fact is missing from the summary, from 0 to 1.
_Avoid_: p_missing, probability, score

**Refute**:
A second look at facts judged partial or absent, which can only clear them, and only with a quote verified in the summary.
_Avoid_: verify, re-check, appeal

**Alarm score**:
A summary's single measure of risk: the highest missing confidence among its critical omissions, with partial ones at half weight.
_Avoid_: risk score, omission score

**Threshold level**:
A named alarm score at or above which a summary is flagged, such as strict or moderate. It belongs to the pipeline it was calibrated on.
_Avoid_: cut-off, threshold (when used as a bare number)

**Flagged**:
The outcome for a summary whose alarm score reaches the chosen threshold level.
_Avoid_: failed, rejected, unsafe

## Pipelines

**Pipeline**:
An ordered set of stages that together carry out a check.
_Avoid_: workflow, chain

**Step**:
A role a stage can fill in a pipeline, such as extract, rank, check or refute, with a fixed kind of output.
_Avoid_: phase (and stage, which is a step's place in one pipeline)

**Implementation**:
One interchangeable way of doing a step, such as checking with one model rather than another.
_Avoid_: variant, strategy, backend

**Stage**:
One step in a particular pipeline, with its chosen implementation and settings, switched on or off.
_Avoid_: step (a step is the role; a stage is its use in one pipeline)

**Pipeline spec**:
The saved description of a pipeline: its domain profile and its stages.
_Avoid_: config, recipe

**Preset**:
A pipeline spec that ships with groundline.
_Avoid_: template, default pipeline

**Validated preset**:
A preset whose results have been measured. A run counts as using it only if nothing about the pipeline or profile was changed, apart from choosing a threshold level.
_Avoid_: approved pipeline, certified pipeline
