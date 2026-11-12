# groundline

groundline checks an AI-written summary against the source it summarizes, and reports what the summary gets wrong. It is domain-neutral: what matters in a domain, such as clinical care, comes from a domain profile.

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

## Access

**API key**:
A secret groundline issues to a caller of its API, which the caller presents with each request.
_Avoid_: key (on its own), token, access key, client key

**Anthropic API key**:
The secret groundline itself uses to call Claude. Always called by its full name, so it isn't confused with an API key.
_Avoid_: API key (on its own), key

## The omission check

**Evaluator**:
One kind of check run on a summary. The omission check is the first, and the commission check is the second.
_Avoid_: metric, scorer

**Fact**:
A single point from the source that a complete summary should record, with a quote from the source as its evidence. Its counterpart on the summary side is a claim.
_Avoid_: item, statement

**Omission**:
A fact the summary leaves out or only partly records.
_Avoid_: gap, miss

**Commission**:
A statement in the summary that the source doesn't support, whether it contradicts the source or isn't in it at all. A fact recorded wrongly, such as a wrong dose, is a commission; the omission check may also report the correct fact as partial or absent.
_Avoid_: hallucination, inaccuracy, accuracy error

**Claim**:
A single checkable statement the summary makes, with a quote from the summary as its evidence. Its counterpart on the source side is a fact.
_Avoid_: fact (that is the source side), item, statement

**Support verdict**:
Whether the source supports a claim: supported, unsupported or contradicted. A claim judged unsupported or contradicted is a commission.
_Avoid_: label, status, result

**Unsupported confidence**:
How sure the check is that the source does not support a claim, from 0 to 1.
_Avoid_: not_supported_confidence, probability, score

**Severity**:
How much it would matter if a fact were left out of the summary, or a claim in it were wrong: critical, supporting or peripheral. Facts are graded by the severity rubric.
_Avoid_: importance, priority

**Verdict**:
Whether the summary records a fact: present, partial or absent. It belongs to the omission check; the commission check uses a support verdict.
_Avoid_: label, status, result

**Missing confidence**:
How sure the check is that a fact is missing from the summary, from 0 to 1.
_Avoid_: p_missing, probability, score

**Refute**:
A second look at facts judged partial or absent, or at claims judged unsupported or contradicted, which can only clear them, and only with a quote verified in the text that would back them up: the summary for a fact, the source for a claim.
_Avoid_: verify, re-check, appeal

**Alarm score**:
A summary's single measure of risk: the highest confidence among its critical problems. For the omission check that is the highest missing confidence among its critical omissions, with partial ones at half weight; for the commission check, the highest confidence among its critical commissions.
_Avoid_: risk score

**Threshold level**:
A named alarm score at or above which a summary is flagged, such as strict. It belongs to the pipeline it was calibrated on.
_Avoid_: cut-off, threshold (when used as a bare number)

**Flagged**:
The outcome for a summary whose alarm score reaches the chosen threshold level.
_Avoid_: failed, rejected, unsafe

**Omission score**:
A summary's alarm score from the omission check, turned around and rescaled to run from 0 to 1, so that higher is better and a score below 0.5 means the summary is flagged.
_Avoid_: alarm score, completeness, note score

**Commission score**:
The same rescaled measure, calculated from the commission check's alarm score.
_Avoid_: alarm score, hallucination score, accuracy score

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

## Running groundline

**Deployment**:
A running copy of groundline, with its own database and API keys, that serves callers. An organization may run more than one, such as staging and production.
_Avoid_: install, instance, environment, stack (instance and stack name the cloud resources a deployment runs on)

**Test deployment**:
A deployment a developer creates to try one commit, for internal testing only and never for customers, reachable only from their own computer. Its server and data are deleted when the developer is done, or at a deadline they can extend.
_Avoid_: test server, dev server, staging (a staging deployment serves callers and lasts)
