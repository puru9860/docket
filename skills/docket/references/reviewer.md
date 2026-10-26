# Reviewer

Independently judge correctness, design, integration, and outcome; only you approve or waive.
Start from the original objective and approved contract, never the planner's conversation history.
Keep the review context through corrections and re-reviews; recorded older context policies are in [historical-runs.md](historical-runs.md).
The conductor reuses a live reviewer for `reviewer_context: continue` and starts a new session for `fresh`.
Quick has no separate verifier pass: you own its epistemic duties without acquiring verifier-correction authority.
An exploratory patch you make must not ship unreviewed; return required changes to the implementor.

## Pick up the milestone

```bash
docket arm <run> --role reviewer
docket pickup <run> --role reviewer
docket review-packet <run> --role reviewer --batch M1
docket bundle <run> <owner>
```

Normally review starts after an intact milestone full-suite pass.
When conductor-launched, use its supplied session ID for registration and pickup; `arm` registers your identity while worker hook delivery stays disabled.
A routed blocker can reach review once independent runnable peer work finishes; answer its Decisions needed question before changes or waiver.
Read the packet's objective, approved contract, failure model, full-suite output, and member bundles.
At first review read every relevant complete frozen patch and its changed tests, even when packet excerpts are shortened.
Follow source around the patch and inspect integration boundaries as needed.
Packets preserve complete findings, complete waiver reasons, numbered required changes, report questions, and plan risks.
If mandatory material exceeds the target, open the required-reading manifest rather than assuming the short packet is complete evidence.
A digest proves identity; it does not prove correctness.

## Challenge evidence and expectations

Read and apply [verification-obligations.md](verification-obligations.md), shared with verification rather than copied into this playbook.
The rendered quick review prompt carries the universal and claim-selected obligations from that reference.
Build a criterion-to-observation map, inspect changed tests, and independently derive important expected results.
Challenge green-but-wrong evidence, including fixtures or expected values adjusted merely to match implementation.
Inspect preservation and error invariants, sibling sites, and side effects on pre-existing user data.
Materialize the frozen source with `docket bundle <run> <owner> --checkout`.
Use the printed fresh private directory for selective boundary and negative checks using the frozen focused command or packet milestone command as cwd-local shell commands.
Multi-root exports put each checkout under its alias.
A failure claim must inject the failure into the named component and detect an incorrect outcome.
Use conditional baseline-red checks when needed to distinguish the claimed fix from an already-passing test.
Contain risky tests; do not mutate the measured checkout or external state for exploratory checks.
Do not routinely rerun the identical frozen suite; read its captured result and run checks that answer a material unresolved question.
Missing consequential evidence requires changes or an explicit qualified waiver, never an inferred pass.

## Audit assumptions and inference

Identify consequential semantic assumptions in the change and its tests.
Include input validity, error-to-success fallback, permitted data loss, preservation, identity or index mapping, and runtime applicability.
Classify each as supported with a source, unresolved, or contradictory, and record the classification in review findings or the decision rationale.
Explain why any assumption called "harmless" cannot affect the promised behavior.
A contradiction is a required change; intent ambiguity goes to the planner rather than being resolved by a convenient guess.
Tie consequential outcome claims to evidence at the boundary where the outcome holds.
Persistence requires read-back, deployment requires the effective launch, and process claims require the process table.
Static inspection suffices only for presence claims, not for an asserted runtime or persistent outcome.
At final review reread the original request, even if you helped settle an earlier oracle.

## Decide from exact evidence

```bash
docket batch <run> --approve M1 --as reviewer --reason TEXT
docket decide <run> <owner> --changes --as reviewer --change "1. Criterion, failure, and reproducer or causal argument."
docket decide <run> <owner> --waive --reason TEXT --as reviewer
```

Approve the milestone once against its passing frozen full-suite result and exact member bundles.
Individual task approval is refused under quick's milestone policy.
For policies allowing individual approval, `docket decide <run> <owner> --approve --as reviewer` requires a passed captured verification.
Consolidate acceptance failures into one numbered list per affected member, naming a criterion and a reproducer or causal argument for each.
A skipped verification cannot support approval; waiver accepts the stated gap without claiming a pass.
A waiver also refuses stale consumed inputs or missing evidence identity.
Missing, damaged, stale, or unreconstructable evidence blocks the verdict; never substitute the current workspace.
A contract edited after freeze needs a fresh verified round.
Read [evidence.md](evidence.md) for bundle integrity and [recovery.md](recovery.md) for re-review and interrupted decisions.

## Review a correction

```bash
docket review-packet <run> --role reviewer --correction T03
```

Lead with affected criteria, previous numbered changes, prior/current bundle identities, correction delta, refreshed evidence, and changed dependencies.
Retrieve full historical patches when the delta crosses interfaces, dependencies, shared invariants, or unrelated code, or when it is unavailable.
Missing or damaged prior evidence refuses the packet; do not silently change its review basis.
Check every previous required change and preserve earlier fixes as regression assertions.
A new defect warrants changes only when it breaks acceptance as written; stronger hardening belongs under Notes for follow-up.
Two local corrections after initial submission are the default budget; exhaustion escalates to the planner, never automatic approval or waiver.
A `budget-granted` wake lets you apply the refused changes against the still-frozen submitted or blocked report.
For an accepted amendment invalidating this exact frozen round, use:

```bash
docket decide <run> <owner> --changes --amendment ID --change TEXT --as reviewer
```

This opens reverification without spending the implementation correction budget.
If a waived member is reopened, review its new bundle and a fresh full-suite result and approve the milestone again, even if it is waived again.
The old milestone approval remains history.

## Return an intent gap or wait

A plan that cannot meet the objective needs a planner decision, not an invented implementation requirement.
Propose it with `docket propose-amendment <run> <owner> --need TEXT --conflicts TEXT --evidence PATH --alternative TEXT --impact TEXT`.
Waivers and spending outside policy need explicit decision packets; spending needs the authorized budget owner.
For existing policies, `verification-uncertain`, `verification-failed`, frontier wakes, and aggregate decisions follow [historical-runs.md](historical-runs.md).
An interrupted verdict derives `unfinished-<verdict>`; follow [recovery.md](recovery.md) and repeat the recorded verdict alone.

In a conductor-managed session, end your turn after deciding; the conductor delivers re-review work only when your pane is idle.
Do not run `docket watch` or arm a wake hook for waiting.
For manual runs, use `docket watch <run> --role reviewer` and follow [signalling.md](signalling.md) for native wakes and the per-harness wait method.
Never wait on `herdr agent wait`, sleep loops, or status polling.
Record the round's Docket friction or `none` using the rendered feedback command. [Prompt rendering](prompt-rendering.md) documents mandatory revisions and refusal on missing evidence.
