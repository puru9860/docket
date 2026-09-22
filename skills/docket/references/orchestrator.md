# Orchestrator

The orchestrator turns the approved plan into assignments, supervises delegated
work, reviews implementor reports and diffs, performs its own tasks, and produces
one standardized aggregate report for the planner or final record.

## Standard and quick sessions

New runs start on the standard preset with separate planner, orchestrator, implementor, verifier, and reviewer sessions.
The quick preset merges planning and orchestration into one coordinator session and verification and review into one checker session.
A coordinator performs exactly the orchestration duties described here plus plan ownership.
A checker performs verification and then review as two recorded duties under `combined-checker`.
The merge never uses `--skip-verify`, a blanket `verifier_exempt`, or a legacy completion shortcut.
Every artifact records the merge, and a run that outgrows quick records an escalation request without migrating or recapturing any baseline.

## Assign tasks

Open every task with independent complexity and executor fields:

```bash
docket assign <run> T03 --complexity high --executor implementor \
  --harness opencode --model <model> [--effort <level>]
```

Only `claude`, `codex`, and `opencode` are supported harness values. Codex support
is temporary compatibility. `--model` records the requested model; it is not proof
that the harness actually selected it.

Fill the task goal and acceptance criteria before dispatch. Add constraints,
existing decisions, or starting hints only when already known; do not investigate
the repository merely to populate them. Validate task intent before dispatch:

```bash
docket validate-task <run> <owner>
```

`docket dispatch` enforces the same intent check and refuses before writing a dispatch record, naming the same problems.
An untouched generated task cannot be dispatched and no prompt is bound for it.
A dependency edge that would create a cycle, including a self edge, is refused at ingress by both `docket assign --depends-on` and `docket batch --depends-on` with a diagnostic naming the cycle, and nothing is published.
A batch edge whose source is not a member of that batch is refused at ingress with a diagnostic naming the non-member source, and nothing is published.
The source check runs against the members declared in the same command, so an edge for a member added in that command still succeeds.
A diamond, a chain, and re-declaring an unchanged edge still succeed.
A batch written before the source rule stays readable: dispatch and readiness only read edges keyed by members, so a stored non-member edge is inert and never blocks member work.
Sequence-numbered artifacts are ordered by their number, so attempt 100 always outranks attempt 99.
Do not dispatch a task that fails this gate. The generated `Txx-scope.mdx` belongs
to the implementor. After discovery, `docket scope ... --submit` mechanically
claims its proposed paths. Disjoint scopes are accepted without a supervisor
turn. Only a collision needs orchestration: sequence the tasks, change ownership,
or let the implementor revise and resubmit its capsule. The implementor owns
preflight after scope acceptance. During review, `docket diff` checks the accepted
scope while preserving pre-existing dirty files.

Roots and baselines are settled before dispatch, not at review. Run
`docket roots <run>` before assigning anything and confirm that every checkout the
batch may change is declared: only a declared root is captured, so a linked worktree
or a nested repository that nobody named is invisible to every baseline and every
diff. Declare a missing one with `docket roots <run> --declare <alias>=<path> ...`
while no baseline exists yet; afterwards the declaration is fixed, because an alias
that starts meaning a different checkout invalidates every baseline under it.

`docket assign` then takes the run baseline before it opens the first task and each
task's own baseline as that task is created, both before any artifact exists. If it
cannot capture one in full - no declared root, a missing or re-pointed checkout, an
unreadable index, an untracked file too large to store whole - it refuses and creates
nothing. That is not a lost assignment: fix the workspace, or declare
`evidence_mode: documents-only` for a run that genuinely has no Git evidence, and
assign again.

Create and validate every assignment in the current batch before starting any
implementor or watcher. A task that has not been assigned yet is invisible to
batch readiness and would make earlier work look like a smaller completed batch.
For runs that need dependency ordering or milestone review, declare the batch
explicitly and close it before dispatch:

```bash
docket batch <run> --create B1 --members T01,T02 --depends-on T02:T01 [--milestone]
docket batch <run> --close B1
```

Closed membership never reopens: a task created later cannot join B1 or change
its readiness event, and tasks outside the batch never block it. Under
`five-role-v1` a closed batch whose verified submission blocks a dependent
derives one reviewer frontier for exactly those members, so approving the
frontier lets the dependent dispatch without manual polling. Reviewer packets
cover only owners actually under review, and `--batch B1` scopes one to
exactly that batch's reviewable members. A reopen moves
a batch holding the owner into a new generation and retires the old readiness.

## Dispatch and supervise

Prefer visible Herdr panes when available. Address agents by stable agent name,
not pane ID. Dispatch through Docket so one round has exactly one writer:

```bash
docket session <run> --register --session SESSION --name NAME --role implementor
docket dispatch <run> T03 --session SESSION --agent NAME
```

A retry with the same session and the same registration generation adopts
the same record after a crash; the same session name with a newer generation
is a different writer and is refused with a pointer to `docket resume`.
A different session while one holds the round is refused, as are unmet
dependencies, colliding scope, and exhausted concurrency - each refusal names
its condition. Execution capacity and held scope are counted separately:
releasing a slot never releases accepted scope, which stays owned through
verification and the review boundary. A slot is released when its round is
approved, waived, or completed, when a later round supersedes it, or when a
ready handoff hands the task to a replacement; `docket reconcile` persists
stale releases from the lifecycle documents and names each one, and
`docket status` and `docket health` show the same live count. The cap claim is
serialized across owners with a narrow run-wide lock, so two dispatches under
a cap of one cannot both succeed while an ordinary dispatch still completes
promptly. A new implementor receives its task file, discovery-capsule path, and
implementor playbook. A replacement receives those plus the latest ready handoff,
latest decision when one exists, and task-local diff. The replacement reads these
artifacts directly; the orchestrator does not summarize them. Move a round with
`docket resume`, which checkpoints first, claims capacity under the same
serialized read-check-write as dispatch (refusing with the cap condition when
full, and never refusing a legitimate takeover by its own predecessor slot),
and leaves exactly one live dispatch record for the new writer. A pending
correction round wakes you with one `correction-ready` event per round, whether
a reviewer or a verifier requested it, so the implementor is re-dispatched
instead of stalled.

Arm only the orchestrator role and set `DOCKET_ROLE=orchestrator`:

```bash
docket arm <run> --role orchestrator
```

### Quiet-supervision contract

Normal task activity is silent in both split and combined topology. Send at most
one dispatch notice for a batch, then wait in one blocking operation. Do not run a
model-driven polling loop and do not send user updates for lifecycle observations
such as “working,” “editing,” “running tests,” “still waiting,” or partial task
completion.

To see what is waiting without consuming it, run
`docket events <run> --role orchestrator --peek`. It writes nothing, so a wake stays
deliverable. It is for inspection after an actionable wake or before handing over,
not a substitute for waiting.

Prefer a batched review event via `docket watch <run> --role orchestrator`.
Ordinary submitted reports do not wake separately: Docket waits until every
currently delegated task is submitted or already decided, then emits one batch
wake. With explicit closed batches, readiness follows closed membership and
explicit dependencies instead. Blockers, scope collisions, replacement handoffs,
and stall incidents remain immediate.
When Herdr is the available completion surface, use one long
`herdr agent prompt ... --wait` or `herdr agent wait`; a blocking shell wait does
not require repeated model turns. On a timeout, re-arm the wait without narrating
it. Inspect terminal output or lifecycle state only once after an actionable wake.
Delivery notices are fixed-format (run, role, event, inbox pointer) and never
carry report prose; when no verified turn boundary exists, events stay queued
for explicit inbox pickup (`docket inbox <run> --role orchestrator --claim
--session SESSION`) or a native hook. Pause delivery with
`docket delivery <run> --role orchestrator --pause` when the recipient must not
be interrupted: pausing queues visibly (`--queued`), consumes nothing, and never
pauses implementation. Check execution separately from report state with
`docket health <run>`: quiet work is healthy, and only an explicit stall report
(`--flag-stall`) opens an incident.

Speak to the user during execution only when:

- a blocker requires user authority or a material decision;
- all work is complete; or
- the user explicitly asks for status.

Process an ordinary review-batch wake internally. Approvals, changes requests,
and another wait are not user-facing progress. Speak only if review reveals a
blocker requiring user authority or the whole run is complete.

Read each submitted task report and its task-local diff, and read the frozen bundle
the round was submitted with: `docket bundle <run> <owner>` names the task revision
the work was done against, the baseline it started from, the full root-qualified
patch against that baseline, the resulting source revision, and the verification as
it actually ran, exit code included. On a correction round it also carries a delta
against the previous frozen bundle, which is usually the fastest way to check what
changed since the last review. Check scope, diff coverage, and verification state,
then route the round to its reviewer with the report, diff, bundle digest, and
verifier finding attached. Only the reviewer approves, waives, or requests changes:
do not record any verdict yourself, and transport verifier findings unchanged. A
changes round is opened by the reviewer's decision, which carries the numbered
required changes the implementor must apply next.

Docket keeps the submitted report body untouched and records the reviewer's verdict,
reviewer identity, reason, and a digest of the reviewed body in the numbered
decision artifact. Reviewer reasoning belongs in that decision file, never in the
report. Repeating a decision is safe and does nothing: it never rewrites the
recorded verdict or opens an extra round, and a different verdict for a decided
round is refused.

If a reviewer decision is interrupted - a killed session, a lost machine, a closed
pane - the reviewer repeats the verdict alone. Docket recorded the reviewer
identity, reason, and the whole decision body before it wrote any artifact, so the
retry finishes that decision exactly as written; the reviewer never has to remember
the wording, and a waiver retry does not need its `--reason` again. `docket status`
lists any transition still unfinished. Do not hand-edit a report or decision to
patch one up, and do not switch verdicts to get past one: a different verdict
against an unfinished transition is refused on purpose, and so is a retry that
states anything the transition did not record. That includes supplying a reason
where the interrupted attempt recorded none: the journal is the decision, so
filling one in would publish text nobody decided. If different text is wanted,
finish the transition first and say the rest in the next round.

A reviewer's verdict binds to that bundle's digest, which the decision artifact
records. Docket refuses the decision when the bundle for the round is missing,
damaged, or stale for the body in front of the reviewer; that refusal is the point,
so do not work around it by hand-editing state. Damaged includes a bundle whose
pinned Git trees are no longer readable, which usually means `.bundles/objects`
was deleted: restore it from a backup, or ask the reviewer for a changes round so
the implementor resubmits from intact evidence. Never delete a run's object store
to reclaim space. An earlier bundle is never rewritten: a re-review or a new round
publishes a new address and leaves the superseded one intact.

If Docket says the report changed after review began, the body under review is not
the body a verdict would bind. Read the current report, then either ask for the
reviewed body back or ask the reviewer to re-run the decision with `--re-review`.
That re-runs the full report gate against the new body - required sections,
placeholders, acceptance mapping, scope, and diff coverage - and only then the
task's `verify:` command, and records the evidence it replaced, freezing the
re-reviewed body as its own bundle and recording the superseded one. A changed body
that no longer passes the gate is refused even when the tests pass, and so is a
failing re-verification; the reviewer then requests changes instead.

A blocked report is the exception, and the same one submission makes: it clears the
blocked checks, including its stated question, and skips completion verification.
A blocker caused by a failing command stays reviewable after its body changes, so
the reviewer waives it on its merits rather than on a test run it was never going
to pass.

`docket diff` states its coverage. Treat `diff coverage unavailable` as missing
evidence, not as unchanged work: either restore the Git evidence or, for a run
that genuinely has none, declare `evidence_mode: documents-only` in `plan.mdx` and
review the documents knowingly. Never treat a `git` run whose diff cannot be read
as ready for review; route it back for evidence repair instead.
An unresolved alias, an ambiguous path, a missing root, or an incompletely captured
baseline reports itself by name; fix the declaration or the capsule rather than
reading the task as unchanged.

In a run with more than one checkout root, changed paths are `<alias>:<path>` and a
report must name them that way. Work committed during a task counts as changed, so
a clean worktree is not evidence of an empty diff, and so does a mode-only or
staging change on a path that was already dirty when the task was assigned.
`docket diff <run> run` shows the whole run against the baseline taken before any
implementor edit; use it, not a concatenation of task diffs, when you need the
run-wide picture.

Accepted scope stays owned until the review boundary, so a second task cannot claim
a path while its first owner is still submitted or in a changes round. When one task
must build on another's unapproved work, that is a run-level choice: declare
`provisional_integration: allowed` in `plan.mdx` and have the consumer record it with
`docket depend <run> T04 --on T03`. Only a round whose frozen verification passed can be
consumed; a blocked, skipped, failed, stale, damaged, or patchless bundle is refused,
because the policy allows building on verified work early, not on work nothing verified.
`docket status` then lists the consumption, whether the consumed evidence has moved, and
which consumers have had their review readiness withdrawn because of it. Provisional
readiness is not approval: the reviewer's approval of the consumer approves nothing
about the dependency,
and a dependency that freezes different evidence blocks the consumer's submission and any
accepting verdict - approval and waiver alike - until it is reverified and re-recorded.

Re-recording is not by itself a recovery, and Docket will not let it look like one. A
round freezes the inputs it consumed, and an accepting verdict compares the live record
against that frozen one, so refreshing a pin under a submitted round leaves the verdict
refused. The recovery is a review boundary: the reviewer records `--changes` on the
consumer (as `docket decide <run> <owner> --changes --as reviewer`), the consumer
re-records the input in the fresh round and resubmits, and the reviewer decides the
round that was actually verified against the current evidence. `docket depend`
enforces the same rule and refuses a submitted or blocked consumer outright, naming
that path. For a consumer the reviewer has already approved or waived it refuses too,
and the round stays visibly stale in `docket status`. Reopening decided work is an
audited reviewer transition of its own: a waived task may be reopened by the reviewer
with `docket decide <run> <owner> --reopen --reason TEXT --as reviewer`, which
preserves the prior round, its report body, and the recorded waiver reason, opens exactly
one next round, reclaims accepted scope subject to the usual collision rules (a collision
refuses the reopen rather than partly applying it), and invalidates any aggregate
readiness derived from the waiver. An interrupted reopen is finished by repeating
`--reopen --as reviewer`: the retry needs no restated reason, never opens a second
round, refuses a different reason, and concurrent retries settle as one reopen under
the owner lock.

Do not send implementor reports, lifecycle updates, or per-task commentary to a
separate planner. In combined mode, do not expose them directly to the user either.

### Partial-work continuity

A killed implementor is resumed, not reconstructed from chat:

```bash
docket resume <run> <owner> --session NEW --reason "process killed"
```

Resume checkpoints first, and the checkpoint is mechanical, never a ready
semantic handoff; the replacement rediscovers from the task, scope, and diff.
Model policy has three states in `plan.mdx`: absent means no `primary_model`
and no `fallback_models`, and any model dispatches with one plain line stating
the run has no approved model policy so nothing is being enforced; single means
a `primary_model` with an empty `fallback_models`, and exactly that one model
is approved; list means a primary with fallbacks, and exactly those models are
approved. An initial `docket dispatch --model` outside the approved list is
refused through the same open exception as `docket switch-model`, so policy
cannot be bypassed by a new dispatch. Every transition keeps `model_history`,
and an exception names its owner and stays open. Each dispatch binds its
rendered prompt bytes as a prompt digest recomputable from `docket prompt`
output for the same role and model. Docket records bindings and never launches
models: a new record reads `unobserved` until `docket set-model` verifies the
live harness, and dispatch output says it recorded a binding rather than that
it started anything. Missing cost telemetry stays `unknown`.

Before token exhaustion, compaction, session replacement, or an unavoidable
harness restart, require the current implementor to create and submit a
standardized checkpoint:

```bash
docket handoff <run> <owner>
# Implementor fills the generated Txx-handoff-NN.mdx.
docket handoff <run> <owner> --submit
```

The submit gate requires a resume summary, completed and remaining work, exact
files and symbols, verification state, decisions and risks, and the exact next
action. Wait for the ready-checkpoint event before replacing a live implementor.
Do not relay ordinary progress while waiting; readiness is an orchestrator event,
not a planner or user update.

Prompt the replacement to read, in order: task, accepted discovery capsule,
latest ready checkpoint, latest decision if present, and task-local diff. A ready
checkpoint is continuity evidence, not completion evidence and not a substitute
for the final task report. Do not read or rewrite it merely to relay context.

If an implementor crashes before checkpointing, dispatch a cheap recovery
implementor with the same artifacts. It performs targeted recovery from the
accepted scope and diff, then updates the checkpoint itself. The orchestrator
does not reconstruct repository knowledge.

### Claude Code model and effort

Docket does not whitelist Claude model IDs. Full or otherwise unlisted IDs may be
passed directly at startup together with effort:

```bash
claude --model claude-opus-4-6 --effort high
```

Prefer changing both settings inside the existing Claude Code session rather than
restarting it:

```text
/model claude-opus-4-6
/effort high
```

After startup or an in-session change, verify the displayed active settings and
record them:

```bash
docket set-model <run> <owner> --actual claude-opus-4-6 --effort high
```

Claude Code currently accepts effort values `low`, `medium`, `high`, `xhigh`, and
`max`. Preserve the current session unless it has exited or cannot accept the
slash commands.

### OpenCode model recovery

Immediately after OpenCode starts, read the active model label in its UI before
dispatching the task. A successful `opencode --model ...` launch may silently fall
back when the requested identifier cannot be resolved. Record the verified label,
including a mismatch, through:

```bash
docket set-model <run> <owner> --actual <active-model-label>
```

Do not infer `actual_model` from the launch command.

When an OpenCode implementor hits a rate limit or the chosen model becomes
unavailable, preserve its current process, session, task context, and pane. The
fast selector shortcut is `Ctrl+X`, then `M`. Through Herdr:

```bash
herdr agent send-keys <agent-name> ctrl+x m
```

If the replacement is in Recent, select it with arrow keys and Enter. Otherwise,
type its name into the selector and press Enter. Verify the new active model label,
then run `docket set-model` again; the CLI preserves the model history on both the
task and current report.

The `/models` command (plural) opens the same picker and remains a valid fallback.
Do not stop the implementor or start a new `opencode --model ...` process merely
to change models. Restart only if the OpenCode process exited, the session cannot
open the selector, or an in-place switch was attempted and failed. If restart is
unavoidable, resume the same OpenCode session (`--session`/`--continue`) instead
of discarding context.

## Aggregate report

After every planned task is decided or completed:

```bash
docket assign <run> orch --executor orchestrator --harness <harness>
```

Fill `orch-report-NN.mdx` using its standard sections: executive summary, task
outcomes, changes delivered, integrated verification, exceptions and waivers,
and decisions needed. Submitting it freezes a content-addressed aggregate bundle
that pins the constituent task-round digests and the run-baseline patch; read it
with `docket bundle <run> orch`, and expect `docket status` to report that
aggregate stale if a waived constituent is later reopened. In split mode, submit it for planner review in
legacy runs and for reviewer review in `five-role-v1` runs. In combined
mode, legacy submission records it as completed without a redundant
self-handoff, while `five-role-v1` submission records it as submitted and it
becomes terminal only through a reviewer decision. An `executor: orchestrator`
task behaves the same way: `submitted` in `five-role-v1`, `completed` in
legacy.

## Prompt rendering

Dispatch binds the prompt bytes for the run workflow, role, and stage; the
stage is derived from lifecycle documents and never supplied as authority.
A `five-role-v1` run and a `legacy` run do not receive identical prompts
where authority differs: five-role keeps reviewer-owned approval while legacy
keeps its completion shortcuts. A missing required input refuses rendering
with a diagnostic naming the artifact, and dispatch still records its binding
with the digest explicitly unavailable. Replacement implementors read the
latest ready handoff only; when none is ready the prompt labels recovery as
mechanical and directs targeted rediscovery. Mandatory material is never
truncated. Optional guidance lives under a token budget covering profile,
cards, headers, and separators; zero selects nothing and an oversized first
card is skipped. Model matching is exact and one-run defaults are removed;
unknown models receive only task-relevant cards. The digest record carries
workflow, stage, renderer revision, and every source revision.
