# Orchestrator

Turn the approved plan into assignments, supervise delegated work, review
implementor reports and diffs, perform your own tasks, and produce one
standardized aggregate report.

## Standard and quick sessions

New runs start on the quick preset; the standard preset (`--mode standard`)
keeps separate planner, orchestrator, implementor, verifier, and reviewer
sessions. Quick merges
planning and orchestration into one coordinator and verification and review
into one checker. A coordinator performs exactly these duties plus plan
ownership; a checker performs verification then review as two recorded duties
under `combined-checker`. The merge never uses `--skip-verify`, a blanket
`verifier_exempt`, or a legacy completion shortcut. Every artifact records the
merge, and a run that outgrows quick records an escalation request without
migrating or recapturing any baseline.

## Standard preset window layout

The standard preset builds two herdr windows once, at session setup. The
planner window is a 1:1 vertical split with planner and reviewer, and the
planner creates a new tab for this window. Build this window from the
orchestrator pane in that new tab. Split the orchestrator pane 1:1 right for
the implementor pane, read the new pane id from `result.pane.pane_id`, and
reuse that pane at every dispatch, one implementor at a time. Then split the
orchestrator pane down in half for the verifier and keep that pane id; start
the verifier there when `launch:verifier` arrives. The first split keeps focus on the orchestrator pane, so the second
still targets it. Only the orchestrator starts the verifier and each
implementor; the launch event appears when verification work is pending and no
verifier session has joined. Only the planner starts the reviewer.

```bash
herdr pane split --current --direction right --ratio 0.5 --cwd "$PWD" --no-focus   # JSON: result.pane.pane_id
herdr pane split --current --direction down --ratio 0.5 --cwd "$PWD" --env DOCKET_ROLE=verifier --no-focus   # JSON: result.pane.pane_id
# On launch:verifier, use the verifier pane id saved above.
herdr agent start verifier --kind opencode --pane <pane_id> -- --auto
```

The planner window uses the same 1:1 right split plus a new tab, built from
the planner pane, with the reviewer started in the split pane.

```bash
herdr pane split --current --direction right --ratio 0.5 --cwd "$PWD" --env DOCKET_ROLE=reviewer --no-focus   # JSON: result.pane.pane_id
herdr tab create --cwd "$PWD" --env DOCKET_ROLE=orchestrator --no-focus   # JSON: result.root_pane.pane_id
# On launch:reviewer, use the reviewer pane id saved above.
herdr agent start reviewer --kind opencode --pane <pane_id> -- --auto
```

Quick uses none of these commands.

## Assign tasks

Open every task with independent complexity and executor fields:

```bash
docket assign <run> T03 --complexity high --executor implementor \
  --harness opencode --model <model> [--effort <level>]
```

Only `claude`, `codex`, and `opencode` are supported; Codex is temporary
compatibility. `--model` records the request; it never proves the harness
selected it.

Fill the goal and criteria before dispatch, with `docket assign ... --goal
TEXT --criterion TEXT` (repeat per criterion) or by editing the task. Add
constraints, decisions, or hints only when already known; never investigate
just to populate them. Validate intent before dispatch:

```bash
docket validate-task <run> <owner>
```

`docket dispatch` enforces the same check and refuses before writing anything,
naming the same problems. An untouched generated task cannot dispatch and binds
no prompt. A dependency edge that would create a cycle, including a self edge,
is refused at ingress by both `docket assign --depends-on` and `docket batch
--depends-on` with a diagnostic naming the cycle, and nothing is published. A
batch edge whose source is not a member is refused the same way. The source
check runs against members declared in the same command, so an edge for a
newly added member still succeeds. A diamond, a chain, and re-declaring an
unchanged edge still succeed. A batch written before the source rule stays
readable: dispatch and readiness read only member-keyed edges, so a stored
non-member edge is inert. Numbered artifacts order by number, so attempt 100
outranks 99. Do not dispatch a task that fails this gate. The generated
`Txx-scope.mdx` belongs to the implementor; `docket scope ... --submit`
mechanically claims its paths. Disjoint scopes need no supervisor turn. Only a
collision needs you: sequence the tasks, change ownership, or have the
implementor revise and resubmit. The implementor owns preflight after scope
acceptance. At review, `docket diff` checks accepted scope while preserving
pre-existing dirt.

Settle roots and baselines before dispatch, not at review. Run `docket roots
<run>` before assigning and confirm every checkout the batch may change is
declared: only a declared root is captured, so an unnamed worktree or nested
repository is invisible to every baseline and diff. Declare a missing one with
`docket roots <run> --declare <alias>=<path> ...` while no baseline exists;
afterwards the declaration is fixed, because a repointed alias invalidates
every baseline under it.

`docket assign` takes the run baseline before opening the first task, and
`docket dispatch` takes each delegated task's baseline before any record
exists. Work finished before dispatch is therefore the starting point, not the
task's change, so dispatch the next task only once prior work is done. If
either command cannot capture a baseline in full - no declared root, a missing
or re-pointed checkout, an unreadable index, an oversized untracked file - it
refuses and writes nothing. That is not lost: fix the workspace, or declare
`evidence_mode: documents-only` for a run with no Git evidence, and retry.

Create and validate every assignment in the batch before starting any worker.
An unassigned task is invisible to readiness and would make earlier work look
like a smaller completed batch. For dependency ordering or milestone review,
declare the batch explicitly and close it before dispatch:

```bash
docket batch <run> --create B1 --members T01,T02 --depends-on T02:T01 [--milestone]
docket batch <run> --close B1
```

In a tiered standard run, a verifier pass frees the slot and scope for the
next dependent; a failure can open a local correction. Once members settle,
`batch:B1:verify-ready` wakes you to run
`docket batch <run> --verify B1 --command 'tests/test.sh'`. This event retires
while verification holds the batch lock and when a current result exists.
A passing suite wakes the reviewer directly. A failed suite wakes you for
integration recovery. Once all tasks are decided, you receive the aggregate wake.

Closed membership never reopens: a later task cannot join B1 or change its
readiness, and outside tasks never block it. Under `five-role-v1` with the
earlier independent policy, a closed batch whose verified submission blocks a
dependent derives one reviewer frontier for exactly those members, so approving
it lets the dependent dispatch without polling. Reviewer packets cover only
owners under review, and `--batch B1` scopes to exactly that batch's members.
A reopen moves holding batches to a new generation and retires old readiness.

## Dispatch and supervise

Prefer visible Herdr panes; address agents by stable name, not pane ID.
Dispatch through Docket so one round has exactly one writer:

```bash
docket dispatch <run> T03 --session SESSION --agent NAME --register
```

`--register` registers a new session inline and reuses a matching one, so a
retried dispatch stays the same writer; `docket session --register` starts a
new generation. Dispatch writes the bound prompt to `.prompts/` and prints the
path: send that file, never re-render. In standard, start the implementor in
the layout's implementor pane (`herdr agent start <name> --kind opencode
--pane <implementor-pane-id> -- ...`), one at a time.

A retry with the same session and generation adopts the record after a crash;
the same name with a newer generation is a different writer and is refused
toward `docket resume`. A different session while one holds the round is
refused, as are unmet dependencies, colliding scope, and exhausted concurrency
- each refusal names its condition. Capacity and scope count separately:
releasing a slot never releases accepted scope, which stays owned through the
review boundary. A slot releases on approve, waive, complete, supersession, or
ready handoff; `docket reconcile` persists stale releases and names each one,
and `docket status` and `docket health` agree on the live count. Cap claims
serialize under a narrow run-wide lock, so two dispatches under a cap of one
cannot both succeed while an ordinary dispatch still completes promptly. A new
implementor gets its task file, capsule path, and playbook; a replacement also
gets the latest ready handoff, latest decision when present, and task-local
diff, which it reads directly - never summarize them. Move a round with
`docket resume`: it checkpoints first, claims capacity like dispatch (refusing
on a full cap, never refusing a legitimate takeover by its predecessor slot),
and leaves exactly one live record. A pending correction wakes you with one
`correction-ready` event per round, reviewer- or verifier-requested, so you
re-dispatch instead of stalling; binding the correction retires the event. An
orchestrator-owned correction and aggregate changes wake you the same way. A
resume launches a new session under current model policy like a dispatch: a
recorded model the plan no longer approves is refused, and `docket resume
<run> <owner> --session NEW --model <approved>` names the replacement in
`model_history`.

Arm only the orchestrator role and set `DOCKET_ROLE=orchestrator`:

```bash
docket arm <run> --role orchestrator
```

### Quiet-supervision contract

Normal task activity is silent in both topologies. Send at most one dispatch
notice per batch, then wait in one blocking operation. Never poll and never
send user updates for lifecycle observations such as "working," "editing,"
"running tests," "still waiting," or partial completion.

Inspect without consuming via `docket events <run> --role orchestrator
--peek`: it writes nothing, so a wake stays deliverable. Use it after a wake
or before handover, never as the wait itself.

Wait with `docket watch <run> --role orchestrator`, following the per-harness table in `docket help signalling`.
Ordinary submissions do not wake separately: Docket waits until every
delegated task is submitted or decided, then emits one batch wake. Under
`five-role-v1` it also waits until every submitted member's verification
resolves, so the wake always carries routable verified work. Closed batches
follow closed membership and explicit dependencies instead. Blockers,
collisions, handoffs, and stalls stay immediate. A blocked implementor round
wakes you first so you can answer your part; only the reviewer settles it.
Hand it over with `docket route <run> --kind blocked --owner T01 --note
TEXT`, which queues a durable notification for the reviewer. For a
requirement conflict, direct the implementor to `docket
propose-amendment` with the decision and evidence; the planner owns contract
changes. Continue unaffected tasks meanwhile. Arm the reviewer to receive it.
Do not wait on `herdr agent wait`, `herdr agent prompt ... --wait`, or a `sleep` loop.
They return on idle or timers, not on lifecycle need, and every return costs a
full-context turn. On Codex, wait exactly one of two ways: with the Stop hook
installed, trusted, and `DOCKET_ROLE=orchestrator` in your environment, end
your turn and the hook hands you the wake free; otherwise run `docket watch
<run> --role orchestrator` with no `--timeout` and pass its printed
`yield_time_ms` (3600000 with `background_terminal_max_timeout = 3600000`) to
every `write_stdin` and `wait` on that command. Never shorten either window:
herdr's `--wait --timeout 120` is for finishing commands, and inside a harness
`docket watch` refuses `--timeout` under 590 seconds. Prefixing one command
with `DOCKET_ROLE=orchestrator` sets nothing; `docket watch` says when the
hook is inert for that reason. On timeout, re-arm silently. Inspect state once
per actionable wake. Delivery notices are fixed-format and never carry prose;
without a verified turn boundary, events queue for explicit pickup (`docket
inbox <run> --role orchestrator --claim --session SESSION`) or a native hook.
Pause with `docket delivery <run> --role orchestrator --pause` when the
recipient must not be interrupted: pausing queues visibly (`--queued`),
consumes nothing, and never pauses implementation. Check execution with
`docket health <run>`: quiet work is healthy, and only `--flag-stall` opens an
incident.

Speak to the user only when a blocker needs user authority, all work is
complete, or status was explicitly requested. Process review-batch wakes
internally; approvals, changes, and further waits are not user progress.

Read each report with its task-local diff and frozen bundle: `docket bundle
<run> <owner>` names the task revision, starting baseline, full
root-qualified patch, resulting source, and captured verification with exit
code. A correction round also carries the delta against the previous bundle -
usually the fastest review path. Check scope, coverage, and verification, then
route to the reviewer with report, diff, bundle digest, and verifier finding.
Only the reviewer approves, waives, or requests changes: record no verdict
yourself, and transport verifier findings unchanged. A changes round opens via
the reviewer's decision with numbered required changes.

Docket leaves the submitted body untouched and records verdict, identity,
reason, and body digest in the decision artifact; reviewer reasoning belongs
there, never in the report. Repeating a decision is safe and inert: it never
rewrites the verdict or opens a round, and a different verdict for a decided
round is refused.

If a decision is interrupted, the reviewer repeats the verdict alone. Identity,
reason, and body were journalled before any artifact write, so the retry
finishes exactly as written; a waiver retry needs no `--reason` again.
`docket status` lists unfinished transitions. Never hand-edit a report or
decision, and never switch verdicts: a different verdict against an unfinished
transition is refused, as is a retry stating anything unrecorded - including a
reason where none was recorded, since the journal is the decision. To say
more, finish first and use the next round.

A verdict binds to its bundle digest. Docket refuses a decision when the bundle
is missing, damaged, or stale for the body in front of the reviewer; that
refusal is the point, so never work around it by hand-editing. Damaged
includes unreadable pinned Git trees, usually a deleted `.bundles/objects`:
restore from backup, or take a changes round so the implementor resubmits from
intact evidence. Never delete the object store for space. Earlier bundles are
never rewritten: re-reviews and new rounds publish new addresses.

If the report changed after review began, read the current body, then restore
the reviewed body or ask the reviewer for `--re-review`. That re-runs the full
gate - sections, placeholders, acceptance, scope, coverage - then the
`verify:` command, freezes the new body as its own bundle, and records the
superseded one. A body failing the gate or re-verification is refused even
with green tests; the reviewer requests changes instead.

Blocked reports skip completion verification at submission and re-review, so a
blocker from a failing command stays reviewable and waivable on its merits.

`docket diff` states its coverage. Treat `unavailable` as missing evidence,
not clean work: restore Git evidence or, for a run with none, declare
`evidence_mode: documents-only` and review knowingly. Never treat an unreadable
`git` diff as ready; route it back for repair. An unresolved alias, ambiguous
path, missing root, or incomplete baseline names itself; fix the declaration
or capsule instead of reading the task as unchanged.

With more than one root, changed paths are `<alias>:<path>` and reports must
name them so. Committed work counts as changed, so a clean worktree never
proves an empty diff; neither does a mode-only or staging change on a path
dirty at assignment. `docket diff <run> run` shows the whole run against the
pre-work baseline; use it for the run-wide picture, not concatenated task
diffs.

Accepted scope stays owned through the review boundary, so no second task
claims a path while its owner is submitted or in a changes round. Building on
unapproved work is a run-level choice: declare `provisional_integration:
allowed` and record with `docket depend <run> T04 --on T03`. Only a passed,
current, patch-bearing bundle is consumable; blocked, skipped, failed, stale,
damaged, or patchless bundles are refused, since the policy covers verified
work early, not unverified work. `docket status` lists consumption, movement,
and withdrawn readiness. Provisional readiness is not approval: approving the
consumer approves nothing about the dependency, and a dependency freezing new
evidence blocks the consumer's submission and any accepting verdict until it
is reverified and re-recorded.

Re-recording is never recovery by itself. Rounds freeze consumed inputs and
accepting verdicts compare live against frozen, so a refreshed pin under a
submitted round still refuses. Recover at a review boundary: the reviewer records `--changes` on the
consumer (`docket decide <run> <owner> --changes --as reviewer`), the consumer
re-records in the fresh round and resubmits, and
the reviewer decides the round verified against current evidence. `docket
depend` refuses submitted or blocked consumers outright, naming that path, and
refuses decided ones too, leaving the round visibly stale. Reopening decided
work is its own audited reviewer transition: `docket decide <run> <owner>
--reopen --reason TEXT --as reviewer` preserves the prior round, body, and
waiver reason, opens exactly one next round, reclaims scope under collision
rules (a collision refuses rather than partly applying), and invalidates
derived aggregate readiness. Finish an interrupted reopen by repeating
`--reopen --as reviewer`: no restated reason needed, never a second round,
never a different reason; concurrent retries settle as one under the owner
lock.

Send no implementor reports, lifecycle updates, or per-task commentary to a
separate planner, and in combined mode do not expose them to the user.

### Partial-work continuity

Resume a killed implementor; never reconstruct from chat:

```bash
docket resume <run> <owner> --session NEW --reason "process killed"
```

Resume checkpoints first; the checkpoint is mechanical, never a ready handoff,
and the replacement rediscovers from task, scope, and diff. On an exhausted
Codex window for a draft task, launch the named approved fallback and run:

```bash
docket resume <run> <owner> --session NEW --register --on-limit --harness opencode
```

Use `--harness claude` for a Claude model. The command picks the next approved
non-Codex fallback, checkpoints the old session, updates harness and model,
and prints the replacement prompt; send those exact bytes and record the live
model with `docket set-model`. Never amend the plan for this handover. Without
an approved fallback, the event asks the planner for a policy decision. The
limit path needs a live exhausted window and a draft round; ordinary resumes
use `docket resume <run> <owner> --session NEW`. Model policy in `plan.mdx`
has three states: absent (no `primary_model` or `fallback_models`, anything
dispatches with one plain unenforced line), single (one primary, empty
fallbacks: exactly that model), list (primary plus fallbacks: exactly those).
A `docket dispatch --model` outside the list is refused like
`docket switch-model`, so no dispatch bypasses policy. Every transition keeps
`model_history` with open exceptions named by owner. Each dispatch binds its
prompt bytes as a digest recomputable from `docket prompt`; Docket records
bindings and never launches models - a new record reads `unobserved` until
`docket set-model` verifies the harness. Missing telemetry stays `unknown`.

Before exhaustion, compaction, replacement, or restart, require a standardized
checkpoint:

```bash
docket handoff <run> <owner>
# Implementor fills the generated Txx-handoff-NN.mdx.
docket handoff <run> <owner> --submit
```

The gate requires resume summary, completed and remaining work, files and
symbols, verification state, decisions and risks, and the exact next action.
Wait for the ready-checkpoint event before replacing anyone; never relay
waiting progress as planner or user updates.

The replacement reads, in order: task, accepted capsule, latest ready
checkpoint, latest decision if present, task-local diff. A ready checkpoint is
continuity evidence, not completion evidence and no substitute for the report;
never read or rewrite it just to relay context.

On a crash before checkpointing, dispatch a cheap recovery implementor with
the same artifacts for targeted recovery from scope and diff; it updates the
checkpoint itself. Never reconstruct repository knowledge yourself.

### Claude Code model and effort

Docket does not whitelist Claude IDs; pass full IDs at startup with effort:

```bash
claude --model claude-opus-4-6 --effort high
```

Prefer in-session changes over restarts:

```text
/model claude-opus-4-6
/effort high
```

Then verify the displayed settings and record them:

```bash
docket set-model <run> <owner> --actual claude-opus-4-6 --effort high
```

Effort values are `low`, `medium`, `high`, `xhigh`, `max`. Keep the session
unless it exited or rejects the slash commands.

### OpenCode model recovery

After OpenCode starts, read its UI model label before dispatching: a
successful `opencode --model ...` launch may silently fall back on an
unresolvable identifier. Record the verified label, mismatch included:

```bash
docket set-model <run> <owner> --actual <active-model-label>
```

Never infer `actual_model` from the launch command.

On a rate limit or unavailable model, preserve the process, session, context,
and pane. The fast selector is `Ctrl+X`, then `M`; through Herdr:

```bash
herdr agent send-keys <agent-name> ctrl+x m
```

Pick from Recent or type the name, verify the new label, and run
`docket set-model` again; history is preserved on task and report. `/models`
remains a fallback. Never stop the implementor or start a new `opencode
--model ...` just to change models; restart only on process exit, an
unopenable selector, or a failed in-place switch. Then resume the same session
(`--session`/`--continue`), never discard context. Warning: OpenCode
`--continue` restores the original model and beats `--model`; a resumed
session ignores the flag. Verify the live label with `docket set-model`,
never trust `--model`.

## Aggregate report

After every planned task is decided or completed:

```bash
docket assign <run> orch --executor orchestrator --harness <harness>
```

Fill `orch-report-NN.mdx` (executive summary, task outcomes, changes
delivered, integrated verification, exceptions and waivers, decisions needed).
Submitting freezes a content-addressed aggregate bundle pinning constituent
digests and the run-baseline patch; read it with `docket bundle <run> orch`,
and expect
`docket status` to report staleness when a waived constituent reopens. In
split mode submit for planner review (legacy) or reviewer review
(`five-role-v1`); in combined mode legacy records `completed` without
self-handoff while `five-role-v1` records `submitted` until a reviewer
decides. An `executor: orchestrator` task matches: `submitted` in
`five-role-v1`, `completed` in legacy.

## Prompt rendering

Dispatch binds prompt bytes for workflow, role, and derived stage; stage is
never supplied as authority. Five-role and legacy prompts differ where
authority differs. Missing inputs refuse with a naming diagnostic while
dispatch records the binding with digest unavailable. Replacements read only
the latest ready handoff, else mechanical recovery with targeted rediscovery.
Mandatory material is never truncated; optional guidance lives under a token
budget (zero selects nothing, oversized first cards skip). Model matching is
exact with no one-run defaults; unknown models get only task-relevant cards.
The digest record carries workflow, stage, renderer, and source revisions.
