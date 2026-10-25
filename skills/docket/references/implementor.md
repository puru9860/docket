# Implementor

Implement assigned tasks one round at a time and never approve your own work.
In quick you coordinate task dispatch and milestone checks within your assigned milestone.
In a conductor-managed session, the conductor owns reviewer launch and milestone handoff.
The planner has already assigned every task, closed each milestone, and declared its full-suite command.
New quick runs record `round_context: shared`: work the tasks and corrections in your session without subagents.
For an existing recorded policy or `fresh` context, follow [historical-runs.md](historical-runs.md) and dispatch's exact instruction.

## Pick up and dispatch the next task

```bash
docket arm <run> --role implementor
docket pickup <run> --role implementor
docket status <run> --role implementor
docket dispatch <run> <owner> --session <session> --agent <agent> --register
```

Register the first dispatch; reuse that session for subsequent dispatches.
When launched by the conductor, use its supplied session ID and pickup command; dispatch registers your identity, never the launch request.
Read the bound prompt and complete task contract, including decisions and discovery constraints.
Begin discovery with project guidance, manifests, and task-specific searches; follow relevant callers, dependencies, and tests.
Stop when you can explain the likely change surface and verification; explore more only when the task needs it.
If a contract changes after dispatch, repeat dispatch to bind the new prompt before working.
Confirm the active harness model and effort rather than trusting launch flags; record the observed identity with `docket set-model`.

## Claim scope before editing

Fill `Txx-scope.mdx` with paths, verification, symbols, and discovery reasoning.
Your proposed `verify:` adds to the assigned command; it never replaces a failing assigned check.

```bash
docket scope <run> <owner> --submit
docket preflight <run> <owner>
```

A clean claim continues without supervisor approval; on collision stop for orchestration.
Amend and resubmit scope if discovery needs another path; the original baseline stays immutable.
Use `docket roots <run>` and `<alias>:<path>` for multiple checkouts.
An undeclared checkout is a blocker, because declarations cannot change after baseline capture. [Evidence rules](evidence.md) explain deepest-root resolution, consumed inputs, and unavailable coverage.

## Implement and prove the contract

Stay within accepted scope and read [verification-obligations.md](verification-obligations.md) before writing checks.
Independently derive important expectations; inspect changed tests for false positives, preservation, and error behavior.
Use different inputs and zero/one/many boundaries when relevant; a fake proves only logic when the criterion names a real system.
Review your own patch for unrequested behavior, missing sibling sites, untested branches, flaky timing, stale docs, and overstated claims.
For a correction, reproduce each required change, preserve earlier fixes as regression assertions, then cite the same check after the fix.
Use decisive negative controls in a disposable copy; prove a claimed failure was injected into the named component and save the failing observation.
Never mutate the measured checkout to run a negative control.
`docket submit` runs the registered `verify:` command once against the final source and freezes its output.
Run extra focused checks for debugging or concrete risk; an identical manual run before submission is unnecessary.
The full suite belongs once per milestone, never in a task round.
Run preflight and submission with the longest available shell timeout.
When the shell cap is shorter than the rendered allowance, use `docket submit <run> <owner> --background` with `--as implementor`.
Read its private log or status; never retry a verification that may still be running.

Record a consumed input before relying on it and while the round is a draft:

```bash
docket depend <run> <owner> --on <other>
```

Only passed, current, patch-bearing evidence is consumable; cross-milestone inputs require intact milestone approval in new runs.
A moved input after submission needs reviewer changes, a new pin, and another verified submission.
Re-recording a pin is never reverification; see [recovery.md](recovery.md).

## Submit completion or an honest blocker

Fill summary, every changed file, exact acceptance boxes, verification, and decisions needed.
Keep criterion wording unchanged; check a box only when the result actually holds. [Evidence and report format](evidence.md) gives the optional stable-ID evidence table and model/environment provenance rules.
Normal completion needs all required criteria met; missing evidence is a gap, never success.

```bash
docket submit <run> <owner> --check
docket submit <run> <owner> --as implementor
# If completion is blocked, state the question under Decisions needed and use:
docket submit <run> <owner> --blocked --as implementor
```

`--check` lists gate refusals without running verification, freezing, or changing state.
For a conflicting or impossible requirement, ask the planner for the smallest contract decision:

```bash
docket propose-amendment <run> <owner> --need TEXT --conflicts TEXT --evidence PATH --alternative TEXT --impact TEXT
```

Keep working on unaffected work; use `--blocked` when execution cannot proceed.
Do not edit the task contract, submitted report body, or lifecycle `status:` yourself.
Never bypass a failing check with `--skip-verify`; it is for a broken verify command and cannot support approval.
Source or document drift during verification refuses submission while preserving concurrent edits.
Read what froze with `docket bundle <run> <owner>`.
An immutable bundle, its baseline, and its pinned input evidence must remain reconstructable.

## Advance or wait for the milestone

Dispatch the next ready task in the current milestone after the preceding work is done.
In a conductor-managed run, end your turn after submission; the conductor delivers the next task or correction to this same milestone session.
When every member has completed implementation, run the planner-declared suite:

```bash
docket batch <run> --verify M1 --as implementor
```

A full-suite pass wakes the reviewer; a routed blocker may need changes or waiver before the suite can run.
In conductor-managed runs, end your turn after the suite; do not run `docket watch`, arm a wake hook, launch roles, or start another milestone.
The conductor delivers corrections to this session and starts a fresh implementor after intact milestone approval.
For manual runs only, handle `launch:reviewer` through a registered inbox claim and wait with `docket watch <run> --role implementor`.
Follow [launch.md](launch.md) for conductor setup and manual fallback, and [signalling.md](signalling.md) for receipts.
After the final milestone, the planner receives completion; no aggregate decision is needed.
On changes, read the numbered decision and running checklist, apply every item, fill the new report, and resubmit.
If numbered changes cannot render, ask the reviewer to restate the unworkable decision.

## Recover or finish

Before compaction, replacement, or interruption, run `docket handoff <run> <owner>`, fill every section, then `docket handoff <run> <owner> --submit`.
Recover from the task, accepted scope, ready handoff, latest decision, and task-local diff with targeted inspection.
Read [recovery.md](recovery.md) for session generations, model limits, moved inputs, and unfinished submissions.
A run that outgrows quick records an escalation and retains its policy until authorized follow-up.
Record each round's Docket friction or `none` using the rendered feedback command. [Prompt rendering](prompt-rendering.md) explains stages, checkpoints, and refusal on missing mandatory inputs.
