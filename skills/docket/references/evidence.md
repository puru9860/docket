# Evidence and reports

## Scope and baselines

Declare checkout roots as `alias=path` before assignment and capture only declared checkouts.
Declarations are immutable after baseline capture; undeclared, missing, ambiguous, or unreadable roots block diff coverage.
A bare path resolves to the deepest declared root, so a nested repository owns its own paths.
Use `<alias>:<path>` in multi-root changed-path lists and report citations.
Baselines are complete, immutable, and captured before the work they measure without committing or stashing user work.
Scope resubmission never resets the baseline; changed paths include prior rounds and committed, staged, mode-only, or already-dirty edits.
`documents-only` reports diff coverage unavailable; unavailable Git coverage is a rejection rather than an empty diff.

## Frozen submissions

`docket submit` holds one owner lock through verification, freeze, ledger append, and report publication.
Let the workspace and documents settle first; do not edit a report from another window during submission.
A changed checkout, contract, report, accepted scope, or consumed input during verification refuses the submission and leaves the concurrent edit intact.
A verify command that writes into the measured checkout fails the freeze.
A second submission waits and is then refused as already submitted; read background results before retrying.

Each submitted round freezes the task revision, task baseline and reconstruction artifacts, full baseline-relative patch, resulting source, exact report body, consumed inputs, and captured verification into one immutable content-addressed bundle.
Corrections and re-reviews publish new addresses without rewriting or deleting previous bundles.
Reconstructing evidence uses private indexes and object storage under `.docket` and never writes even loose objects into the measured checkout.
Missing artifacts, changed digests, or unavailable pinned trees make evidence DAMAGED, never intact.
A vanished capture fails the freeze rather than leaving an unavailable placeholder in an intact bundle.
A verdict binds to the exact digest and contract revision, never to whatever the workspace currently contains.
Use `docket bundle <run> <owner>` for the frozen patch, report, and verification.
Do not edit a submitted body; corrections belong in a new round.

## Consumed inputs

Record `docket depend <run> <owner> --on <other>` before relying on an input and only while the consumer is a draft.
A passed, current, patch-bearing bundle is mandatory; blocked, skipped, failed, stale, and patchless inputs are refused even if provisional integration is allowed.
Tasks within one closed milestone may consume tiered-settled inputs; `milestone_dependencies: approval-v1` also requires intact approval for a predecessor in another milestone.
Runs without that field and ungrouped tasks retain their recorded dependency and integration policy.
`provisional_integration: forbidden` requires approval outside tiered settlement; `provisional_integration: allowed` permits verified work early but never equates readiness with approval.
The consumed-input record, including the empty set, freezes into the consumer round.
Moved inputs withdraw readiness and block approvals and waivers.
Recovery requires reviewer changes, a new pin in the draft, and a new verified submission.
Editing `.deps/<owner>.json` or refreshing a pin cannot stand in for reverification; acceptance compares live and frozen records.

## Model and environment provenance

Confirm model and effort from the live harness rather than launch flags and record them with `docket set-model`.
Notify the coordinating role when the observed values differ or change.
A dispatch records a prompt binding, never proof of a launch; it stays unobserved until the active identity is confirmed.
Frozen verification labels a model observed, requested, or unknown; a request is never presented as execution identity.
Declare verification inputs in task frontmatter `env:`; declared `KEY=VALUE` overrides ambient state and captures the effective value.
Only declared environment keys are captured.
Changes to command, source, roots, or declared inputs require new verification.
`--skip-verify` is for a broken command with a stated reason; skipped verification never supports approval, and waiver remains the honest accepting path.

## Report format

Fill summary, files changed, acceptance, verification, and decisions needed.
List every path in `docket diff <run> <owner>`, including earlier rounds, with line citations.
Keep acceptance criterion text exactly as assigned and change only checkbox state.
Name observed checks and outcomes; if submission will capture the command, say so instead of inventing a run.
Mark a criterion proven only against a fake as partial if it names a real system, and name that gap.
Inspect screenshots before claiming they prove behavior.

An optional `## Evidence` table has one row per stable criterion ID in task order:

```text
| Criterion | State | Artifacts | Gaps |
| --- | --- | --- | --- |
| A1 | met | verify.stdout | none |
```

States are `met`, `partial`, `not-met`, and `not-verified`.
A `met` row names frozen artifacts or existing paths and carries no gap.
Other states must name the gap.
Duplicate or unknown IDs, missing artifacts, and gaps on `met` fail the structural gate.
Prose is not keyword-policed; qualifiers and integration limits are judged for meaning.
Normal submission requires every required criterion met.
Use `--blocked` or an approved amendment for unmet criteria; these paths stay lightweight and skip completion-evidence enforcement.
`docket submit <run> <owner> --check` reports the exact current gate refusals without running, freezing, or changing anything.
A blocked report needs a stated question and skips completion verification; do not make honest blocking harder than claiming success.
