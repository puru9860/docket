# Implementor

Implement exactly one assigned task.

The implementor role is identical in the standard preset and the quick preset.
In quick runs the coordinator assigns and the checker verifies and reviews, but the implementor still submits through the same gate with the same scope, baseline, and verification binding.
An implementor never approves its own work in either preset.
A run that outgrows quick records an escalation request; the implementor keeps working under the recorded preset until an authorized follow-up decides otherwise.

1. Read the complete `Txx-task.mdx`. Own repository discovery: begin with project
   guidance/manifests and task-specific searches, then follow relevant callers,
   dependencies, and tests. Stop when you can explain the likely change surface
   and verification. Broader exploration is allowed when the task genuinely
   requires it; avoid exhaustive reading that does not improve the decision.
2. Fill `Txx-scope.mdx` with proposed paths, exact verification, relevant symbols,
   and discovery reasoning, then run `docket scope <run> <owner> --submit` before
   editing. A clean claim lets you continue without supervisor approval. On a
   collision, stop for orchestration. If later discovery requires another path,
   amend the capsule and resubmit it; Docket preserves the original diff baseline,
   which was captured when the task was assigned. In a run with more than one
   checkout root, address another checkout as `<alias>:<path>` - run `docket roots
   <run>` for the aliases - and expect changed paths, and the report's *Files
   changed* section, to use the same form. A bare path resolves to the deepest
   declared root containing it, so a nested repository owns its own paths. Only a
   declared root exists as far as Docket is concerned: if the task needs a checkout
   that `docket roots` does not list, that is a blocker for the orchestrator, not a
   path to claim, because the declaration cannot change once a baseline exists.
3. Run `docket preflight <run> <owner>`, implement within the accepted scope, and
   run the registered `verify:` command. If environment or ambiguity prevents
   completion, report a blocker rather than claiming success. `docket submit`
   reports its diff coverage. Unavailable coverage under `evidence_mode: git` is a
   rejection, not an empty diff: fix the workspace so the task diff is readable, or
   report the blocker. Do not describe unverifiable work as verified.
   If the task consumes another task's work, record it with `docket depend <run>
   <owner> --on <other>` before you rely on it. That pins the exact evidence you
   read; if the other task later freezes something different, your own submission is
   refused until you reverify against the new evidence and record it again, and so is
   any approval or waiver of your round while that input is stale. Consuming work that
   is not yet approved is only possible when the run declares
   `provisional_integration: allowed`, and only from a round whose frozen verification
   passed: a blocked, skipped, failed, stale, or patchless bundle is refused. It is
   readiness, never approval. Record it while your round is still a draft: once you
   have submitted, `docket depend` is refused, because the pin is part of the evidence
   the reviewer is reading. If an input moves after you submitted, ask the reviewer for
   a changes round, then re-record and resubmit so the new input is actually verified.
   Editing `.deps/<owner>.json` by hand achieves nothing: an approval compares the live
   record against the one frozen into your round and refuses a mismatch.
4. Confirm the active harness model and effort rather than trusting launch flags.
   The orchestrator records them with `docket set-model`; notify the orchestrator
   if the UI values change or differ from the request. Model policy has three
   states: absent means no approved policy and any model dispatches with one
   plain no-policy line; single means one primary with empty fallbacks and
   exactly that model is approved; list means a primary with fallbacks and
   exactly those models are approved. Docket records bindings and never launches
   models: your dispatch binds its rendered prompt bytes as a prompt digest, a
   new record reads `unobserved` until `docket set-model` verifies you, and
   dispatch output says it recorded a binding rather than that it started
   anything. The frozen verification
   records the model as observed, requested, or unknown - a requested flag is
   never presented as observed execution. Declare environment inputs the
   verification depends on in task frontmatter `env:`; declared `KEY=VALUE` inputs
   override ambient state so the declared value is what runs, and the bundle records
   the effective value each declared key actually had, with only declared keys ever
   captured. Any change to command,
   source, roots, or declared inputs runs verification again, and a verify
   command that writes into the checkout fails the freeze. A skipped verification
   may still be recorded with `--skip-verify` and a reason, but it cannot support an
   approval in any mode; waiving accepts such work without claiming it passed. Read
   `references/verification-obligations.md` before writing checks: two inputs
   with independent expectations, separate actual provenance from oracle
   derivation, byte-sensitive fingerprints, zero/one/many boundaries,
   reviewed oracle changes, and controlled offline proof or an explicit gap.
5. Fill the standard task report: summary, files changed, acceptance,
   verification, and decisions needed. Copy every acceptance criterion exactly;
   only change its checkbox state. Optionally map each criterion to evidence in
   an `## Evidence` table with one row per stable ID (A1, A2, in task order):
   `| A1 | met | verify.stdout | none |`. States are `met`, `partial`,
   `not-met`, or `not-verified`. A `met` row names frozen artifacts or existing
   paths and carries no gaps; anything else names its gap. The gate enforces
   the table mechanically when present - duplicate or unknown IDs, missing
   artifacts, and gaps on `met` fail - but never keyword-polices prose, so
   "no limitations" and integration-coverage notes are judged on structure.
   Normal submission needs every required criterion `met`; anything less goes
   through `--blocked` or an approved amendment, which stay lightweight and
   skip evidence enforcement.
6. Submit normally with `docket submit <run> <owner> --as implementor`, or submit
   a blocker with `docket submit <run> <owner> --blocked --as implementor`. Submitting freezes the whole round -
   the task revision, the task baseline, the full patch from that baseline, the
   resulting source revision, your report body, and the verification as it actually
   ran - into one immutable bundle that the reviewer's verdict will bind to. Let the
   workspace settle first: a checkout that changes while the verify command runs is
   refused, because a green result that does not describe the frozen source proves
   nothing. The same holds for the documents: if the task, your report, your accepted
   scope, or a consumed input moves while the command runs, the submission is refused
   and your edit is left alone. Do not edit the report from a second window while a
   submission is running, and do not run two submissions for one task at once - the
   second waits for the first and is then refused as already submitted. Read what was
   frozen with `docket bundle <run> <owner>`.
7. After submitting, do not edit the report body. It is the evidence the reviewer
   reviews, and a body that changed since review began makes the decision
   unusable until the reviewer puts the new body back through the whole gate -
   sections, acceptance, scope, diff coverage, and verification - which an edit
   made in a hurry will usually fail. A blocked report is re-reviewed against the
   blocked checks only and skips completion verification, exactly as its submission
   did, so an honest blocker stays reviewable; that is not a licence to edit it.
   Corrections belong in the next round, not in a submitted report. Editing it also
   leaves the frozen bundle stale for the body in front of the reviewer, which blocks
   the verdict outright until the whole gate runs again.
8. If changes are requested, read the numbered decision file, apply every item,
   fill the new report round, and resubmit. Every correction round carries
   numbered required changes, including one a verifier opened from its
   findings, so a round that cannot be rendered is never opened: if the prompt
   refuses with a diagnostic about numbered changes, the decision is
   unworkable and the reviewer must restate it.

When resuming, read the task, accepted scope capsule, latest ready handoff, latest
numbered decision if present, and task-local diff. Recover from those artifacts
with targeted inspection; the orchestrator does not provide a repository summary.

Before token exhaustion, compaction, session replacement, or an unavoidable
restart, preserve continuity instead of leaving knowledge in chat or scrollback:

```bash
docket handoff <run> <owner>
# Fill every section of the generated checkpoint.
docket handoff <run> <owner> --submit
```

Record exact files and symbols, what is actually complete, what remains,
verification already run and its result, decisions and risks, and one exact next
action. Submit the checkpoint while there is still enough context to make it
useful. It is not a completion report; resume implementation or yield to the
replacement after it becomes ready. A ready handoff releases execution
capacity but keeps accepted scope held; the replacement re-acquires the slot
with `docket resume`, which leaves exactly one live dispatch record.

A dispatch retry adopts only the same session with the same registration
generation. Re-registering the same session name creates a newer generation,
which is a different writer: the retry is refused and names `docket resume`
as the recovery, while a same-generation retry adopts the same record as
before.

The CLI owns report state transitions. Do not edit `status:` manually.

## Prompt rendering

The implementor prompt is rendered for the run workflow, role, and stage from
the canonical contract: initial implementation, correction, resume,
verification, and review are distinguishable, and the stage is derived from
lifecycle documents rather than supplied by the caller. A correction carries
every numbered required change with bundle and verification pointers. Resume
names only the latest ready handoff and names the mechanical checkpoint when no ready handoff exists; when none is ready it labels recovery as
mechanical and directs targeted rediscovery from the task, the accepted capsule, and the task-local diff instead of naming a draft as
ready. A missing required input refuses with a diagnostic naming the artifact,
never a placeholder. An untouched task whose mandatory contract still carries unresolved template placeholders is refused the same way, as a missing required artifact.
`docket dispatch` runs the same task-intent check as `docket validate-task`, so work never starts against a contract the gate rejects. The mandatory contract is the relevant constraints from the plan, the task, Existing decisions and Discovery constraints, not Out of scope alone; the prompt carries task Out of scope plus Existing decisions plus Discovery constraints plus plan hard constraints and plan risks. Mandatory contract, acceptance, and hard constraints are
never truncated; mandatory size is reported separately and zero guidance budget still carries every mandatory section without dragging playbook prose in. Optional guidance
lives under a token budget covering profile, cards, headers, and separators;
zero selects nothing and an oversized first card is skipped. Selected and
rejected guidance is recorded with reasons. Model matching is exact and
one-run defaults are removed; an unknown model receives only task-relevant
cards. Literal commands, fences, and tables survive unchanged.
