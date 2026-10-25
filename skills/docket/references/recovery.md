# Recovery

Recover from durable artifacts rather than chat, scrollback, pane activity, or guessed session identity.
Never delete `.docket`, rewrite lifecycle `status:`, weaken the gate, recapture a baseline, or overwrite a frozen bundle to recover.

## Conductor and pane recovery

Herdr connection failures retry automatically with backoff; a sustained outage opens a planner-directed incident before the conductor stops.
Restore Herdr and restart `docket conduct RUN`; its worker tab, panes, and deliveries reconcile from recorded transport state.
Use `docket status RUN` for the worker tab, pane IDs, agent names, and last deliveries.
To reopen a closed or exited worker with its native conversation intact:

```bash
docket reopen RUN --milestone M1 --role implementor
docket reopen RUN --milestone M1 --role reviewer
```

This requires a recorded native session, leaves lifecycle and frozen evidence unchanged, and asks the resumed worker to register its new transport session before acting.
The worker uses the ordinary task resume gate when a draft is still bound to its old transport session.
No requested model is recorded as observed, and neither reopen nor stall recovery switches models automatically.
Unknown permission dialogs, provider usage-limit messages, and idle unanswered obligations wake the planner through `.incidents/`.
Inspect and resolve the actual cause, then run `docket health RUN --resolve-stall ID`.
A healthy busy verification or a worker waiting without an obligation is not an idle stall.
See [launch.md](launch.md) for thresholds, startup screens, native resume flags, and the manual launch fallback.

## Interrupted task or context replacement

Before interruption, compaction, token exhaustion, or replacement:

```bash
docket handoff <run> <owner>
# Fill every section, then publish the checkpoint.
docket handoff <run> <owner> --submit
```

Record exact files and symbols, completed work, remaining work, checks and observed results, decisions and risks, and one exact next action.
A ready handoff is not completion; it releases capacity but keeps accepted scope.
A replacement reads the task, accepted scope, latest ready handoff, latest numbered decision, and task-local diff, then performs targeted discovery.
A draft handoff is never presented as ready; the mechanical checkpoint is recovery context only.
An unchanged worker checkpoint renders the initial prompt rather than asking it to rediscover nonexistent work.

```bash
docket resume <run> <owner> --session replacement --agent replacement --register
```

Resume reacquires the slot and leaves exactly one live dispatch record.
A same-generation dispatch retry adopts the existing record.
Re-registering the same session name advances its generation and makes it a different writer; use resume when the refusal names it.
Check `docket resume --help` for model and harness overrides; a resume is a new launch and must satisfy current model policy.

## Long or interrupted submission

Use the longest available shell timeout for preflight, submission, and verification.
The rendered prompt gives the minimum submit allowance, including verification and freeze time.
If the harness shell cap is shorter:

```bash
docket submit <run> <owner> --background --as implementor
```

The child runs the ordinary gate while holding the owner lock and returns a private log path.
Read the log or `docket status <run>` before retrying; never launch a duplicate verification that might still be running.
An already-running background retry reports the existing job.
Drift in source, contract, scope, report, or consumed inputs refuses the freeze; settle those inputs and resubmit the current draft.
A deleted baseline blocks even a lightweight blocked submission: restore the original reconstruction artifacts, never recapture them.

## Interrupted decision or changed review evidence

`docket status <run>` lists unfinished transitions and the wake names their recovery command.
Repeat the recorded verdict alone, including reviewer authority, to finish the exact recorded decision:

```bash
docket decide <run> <owner> --approve --as reviewer
docket decide <run> <owner> --waive --as reviewer
docket decide <run> <owner> --changes --as reviewer
```

Use only the matching command, not all three.
Docket already recorded reason, identity, and required changes; a retry cannot replace wording or add a reason originally absent.
Retry an interrupted batch approval with its same batch command to resume the recorded reason and evidence.
An unstarted decision that published no artifact may be abandoned when its evidence moves; once an approval was published, exact recovery remains required.

A report body edited after review began requires reading it again and either restoring the reviewed body or using `--re-review` with the intended verdict.
Re-review runs the whole report gate and verification before freezing a replacement bundle for the same round.
A blocked report runs only blocked checks and skips completion verification, so it stays honestly reviewable and waivable.
A changed task contract needs a fresh verified round rather than reuse of an old verdict.
Missing or damaged bundles and unavailable pinned trees refuse decisions; restore exact evidence or report the block, never substitute the current tree.

## Moved input or amended contract

Recording an input is draft-only; `docket depend --on` refuses submitted, blocked, and decided consumers.
If an input moves after submission, the reviewer requests changes; the implementor records the new pin in the new draft and submits verification again.
Refreshing a pin in place or editing the live record cannot bypass comparison to frozen inputs.
For a planner-accepted amendment invalidating this frozen bundle:

```bash
docket decide <run> <owner> --changes --amendment ID --change TEXT --as reviewer
```

That reverification round does not spend the implementation correction budget.
Numbered required changes must render before a round opens; ask the reviewer to restate an unworkable decision.
Budget exhaustion creates one escalation for the planner; a grant through `docket escalation` wakes the reviewer to apply the refused changes.
A grant does not alter frozen evidence or imply approval.

## Model policy, usage limits, and waiting

Absent model policy permits dispatch with an explicit no-policy line; single policy approves exactly its primary; list policy approves exactly primary and fallbacks.
Confirm active model and effort rather than trusting flags; OpenCode changes model in the current session with Ctrl+X then M or `/models`, and Claude Code supports `/model` and `/effort`.
Preserve an existing session where possible.
A resume refuses a recorded model the current plan no longer approves; provide an approved `--model`.
For a Codex account limit use the next approved fallback outside Codex with `docket resume <run> <owner> --on-limit --harness HARNESS --model MODEL --session SESSION --register`.
Never silently downgrade an independent reviewer to keep moving; wait for quota or obtain the required policy decision.
Raw model cases do not enter prompts; only profiles reviewed and adopted with `docket models --adopt` provide guidance backed by at least two tasks.

An unarmed role never wakes; check `docket doctor`, arm in the correct session, and read [signalling.md](signalling.md).
An arm refused because `DOCKET_RUN` names another run needs a fresh pane for the role, not a changed binding to hide the mismatch.
The planner's missed-handoff wake names another role's untaken event; it is not a request to relay routine progress.
After native wakes record pickup before acting; an unchanged received event stays quiet, while an undelivered announcement may retry after its lease.
Healthy idle is silent success, not a reason for short waits, status polling, or repeated prompts.
