# Orchestrator

The orchestrator turns the approved plan into assignments, supervises delegated
work, reviews implementor reports and diffs, performs its own tasks, and produces
one standardized aggregate report for the planner or final record.

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

Do not dispatch a task that fails this gate. The generated `Txx-scope.mdx` belongs
to the implementor. After discovery, `docket scope ... --submit` mechanically
claims its proposed paths. Disjoint scopes are accepted without a supervisor
turn. Only a collision needs orchestration: sequence the tasks, change ownership,
or let the implementor revise and resubmit its capsule. The implementor owns
preflight after scope acceptance. During review, `docket diff` checks the accepted
scope while preserving pre-existing dirty files.

## Dispatch and supervise

Prefer visible Herdr panes when available. Address agents by stable agent name,
not pane ID. A new implementor receives its task file, discovery-capsule path, and
implementor playbook. A replacement receives those plus the latest ready handoff,
latest decision when one exists, and task-local diff. The replacement reads these
artifacts directly; the orchestrator does not summarize them.

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

Prefer a report event via `docket watch <run> --role orchestrator`. When Herdr is
the available completion surface, use one long `herdr agent prompt ... --wait` or
`herdr agent wait`; a blocking shell wait does not require repeated model turns.
On a timeout, re-arm the wait without narrating it. Inspect terminal output or
lifecycle state only once after a settled/blocked return or when a report is
missing unexpectedly.

Speak to the user during execution only when:

- a blocker requires user authority or a material decision;
- a reviewed batch has an outcome worth reporting;
- all work is complete; or
- the user explicitly asks for status.

Read each submitted task report and its task-local diff. Approve, waive a genuine
external blocker, or request specific changes. A changes decision is two-phase:
the first command opens the decision draft; fill it, then repeat the command to
apply the transition and open the next report round.

Do not send implementor reports, lifecycle updates, or per-task commentary to a
separate planner. In combined mode, do not expose them directly to the user either.

### Partial-work continuity

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
and decisions needed. In split mode, submit it for planner review. In combined
mode, submission records it as completed without a redundant self-handoff.
