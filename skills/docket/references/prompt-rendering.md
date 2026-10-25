# Prompt rendering

## Contract and stage

Prompts render for the recorded workflow, role, and lifecycle stage from canonical contracts.
Initial implementation, correction, resume, verification, and review are distinguishable.
Stage is derived from lifecycle documents; an explicit stage flag is for inspection only.
Missing required artifacts refuse with a diagnostic naming the input rather than a placeholder.
`docket dispatch` runs the same task-intent check as `docket validate-task`, so unresolved contract placeholders refuse before work starts.

The mandatory contract includes task goal and acceptance, Out of scope, Existing decisions, Discovery constraints, plan hard constraints, and plan risks.
These sections are never truncated; their mandatory size is reported separately.
An aggregate planner prompt carries the real objective, approach, risks, hard constraints, and plan revision rather than task placeholders such as `see task file`.

## Correction and resume

Corrections carry every numbered required change with exact bundle and verification pointers and the running checklist of earlier changes.
Resume names only the latest ready handoff, never a draft.
Without a ready handoff, the prompt names the mechanical checkpoint and directs targeted recovery from the task, accepted scope, and task-local diff.
An untouched checkpoint renders the initial prompt because there is no work to rediscover.
Review prompts name the pinned bundle, verification, and decision revisions; missing required evidence refuses rendering.

## Optional guidance

The guidance budget covers the profile, cards, headers, and separators.
Zero selects no optional guidance while retaining every mandatory section.
An oversized first card is skipped, and selected and rejected guidance is recorded with reasons.
Model matching is exact on the normalized identifier; one-run model defaults are removed and unknown models receive only task-relevant cards.
Literal commands, fences, tables, and evidence pointers survive rendering unchanged.
