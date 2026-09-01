# How docket works

For anyone - human or agent - modifying docket itself. If you only want to *use* it,
read the README and `docket help <role>`.

## The model in one paragraph

Three logical roles (planner, orchestrator, implementor) may run as separate agents, or
the planner and orchestrator may be combined. Supported harnesses are Claude Code,
Codex (temporary compatibility), and OpenCode.
They coordinate entirely through Markdown files in `.docket/runs/<run>/`.
A supervisor learns that a subordinate finished because the *harness* re-enters it, not
because it polled.
Mechanical gates validate task intent, atomically claim implementor-discovered scopes,
detect active-scope collisions, and refuse to mark a report reviewable unless its schema,
diff boundary, acceptance fidelity, and verification all hold.

## Why files, not messages

Three constraints forced this, and they are worth keeping in mind before you replace it
with something cleverer.

- **Harness independence.** A planner on Claude Code and an implementor on OpenCode share
  no API. A directory is the only interface both can use.
- **Terminal output is lossy.** Agents render on the terminal's alternate screen; rows
  that scroll off are unrecoverable. So a supervisor can never reliably read a
  subordinate's *output* - it must read a file the subordinate wrote.
- **Sessions die.** A file-based run survives a killed session, a compacted context, and
  a machine restart. In-memory coordination does not.

## Data model

```
.docket/
  watch.conf                 armed (run, role) pairs, one per line
  templates/                 optional per-project schema overrides
  runs/<run>/
    plan.mdx                 planner output; functional tasks and ownership
    <owner>-task.mdx         assignment intent, constraints, and optional hints
    <owner>-scope.mdx        implementor discovery capsule and claimed paths
    <owner>-handoff-NN.mdx   resumable partial-work checkpoint
    <owner>-report-NN.mdx    the subordinate's report for round NN
    <owner>-decision-NN.mdx  the reviewer's verdict for round NN
    docs/                    supporting material
    .snapshots/<owner>.json  task-local diff baseline and accepted scope
    .baselines/<owner>.json  preflight verification result
    .woke-<role>             per-role wake ledger, append-only
```

`owner` is a task id (`T03`) or the literal `orch`.
Round numbers are **per-owner**, so `T03-decision-02.mdx` is unambiguously round 2 of T03.

**Frontmatter is the single source of truth.** There is deliberately no `state.json`.
A parallel state file drifts from the documents agents actually edit, and then you have
two truths and no way to tell which is stale. If you need new state, add a frontmatter key.

The frontmatter parser (`parse`/`render` in `bin/docket`) is intentionally a flat
`key: value` reader, not YAML. It has no dependencies and cannot execute anything. Keep it
that way; nothing in the protocol needs nested structures.

## Lifecycle

```
                    docket assign
                          |
                          v
                implementor discovery
                          |
                  docket scope --submit
                          |
                    scope ready
                          |
                       draft ------------------.
                          |                    | implementor sets
              docket submit (THE GATE)         | status: blocked
                          |                    | + states a question
                          v                    v
                     submitted              blocked
                          |                    |
                          '------- docket decide -------.
                                                        |
                    .-----------------------------------'
                    |                                   |
              --approve                            --changes
                    |                                   |
                    v                                   v
                approved                        changes-requested
                                                        |
                                          opens <owner>-report-NN+1.mdx
                                                        |
                                                        '--> draft (next round)
```

Only `submit` moves a report out of `draft`, and only `decide` moves it out of
`submitted`/`blocked`. Nothing else may write `status:`.

## The gate

`cmd_submit` is the load-bearing function. In order, it rejects a report that:

1. has a `status:` other than `draft` or `blocked` (so a report cannot be submitted twice)
2. is missing any of `REQUIRED_REPORT_SECTIONS`, or has one that is empty once HTML
   comments are stripped
3. still contains a placeholder (`<!-- TODO`, `TODO:`, `FIXME`, `XXX`)
4. has unchecked acceptance criteria, or no checked ones - unless `status: blocked`
5. is `blocked` but says "none" under *Decisions needed* - a block must state its question
6. changes paths outside the accepted implementor scope
7. omits task-local changed files from the report
8. fails the task's `verify:` command

Verify runs **last, and only if the structural and scope checks passed**. Spending a test
run on a report that is already incomplete is waste, and the ordering means the failure a
subordinate sees is the most fundamental one first.

`status: blocked` is a designed escape hatch, not a failure path. It lets a subordinate
stop honestly instead of fabricating a pass, which is the single most valuable behaviour
the gate buys. Do not make blocking harder than submitting.

## Signalling

A supervisor must be re-entered while **idle**, having already ended its turn. Three
approaches were considered:

| Approach | Why it loses |
| --- | --- |
| Interval polling from inside the agent | Each poll costs a turn and tokens, and it cannot wake an idle session |
| A long-poll the agent re-enters itself | Depends on the model voluntarily calling a blocking command forever - the one thing a prompt cannot guarantee |
| **Harness re-entry (chosen)** | The harness wakes the model. Nothing depends on model compliance |

On Claude Code that is a `Stop` hook with `asyncRewake: true`:

> If `true`, runs in the background and wakes Claude on exit code 2. The hook's stderr is
> shown to Claude as a system reminder.

So `hooks/wake.sh` runs `docket watch --armed`, which sleeps at zero token cost and exits
2 with a banner on stderr when something is actionable. This design is
[firstmate's](https://github.com/kunchenguid/firstmate); see the README acknowledgements.

Three rules keep it correct:

- **Foreground only.** The watcher runs in the hook's own process tree. Never `&`,
  `nohup`, or `disown`: the harness owns the process group and relies on that to tear the
  watcher down with the session. A backgrounded watcher outlives its session and wakes
  the wrong one.
- **Per-role ledgers.** `.woke-<role>` is append-only, one file per role. A shared ledger
  let whichever session woke first consume an event the other role needed - that was a
  real shipped bug.
- **Every waiting role must be armed.** An unarmed role is never woken, silently.
  `docket doctor` warns when only one role is armed.

Events come from `events(run_dir, role)`; the orchestrator sees task owners, the planner
sees `orch`. To add an event type, add it there and give it a stable key - the key is what
the ledger dedupes on, so an unstable key causes a wake loop.

## Invariants

Break these and the system stops being trustworthy.

1. `docket submit` is the only path to a reviewer-visible state. Never hand-edit `status:`,
   and never pass `--skip-verify` to get a report through.
2. The submitted report and task-local diff are review truth. The discovery capsule and
   ready checkpoint are implementation context, not completion evidence.
3. A `verify:` command must run under `/bin/sh` with no aliases, shell functions, or
   personal `PATH` additions. A command written in an interactive zsh session can
   reference a function that does not exist in `sh`, and will then fail the gate forever.
4. The watcher runs in the hook's foreground process tree. See above.
5. Ledgers are per-role and append-only. Deduping is what prevents wake loops.
6. Round numbers are per-owner and monotonic. Only `decide --changes` opens a round.
7. Document frontmatter is lifecycle state. Snapshot and baseline JSON contain only
   mechanical diff and verification evidence, never competing lifecycle state.
8. A reviewer reads the diff, not only the report. The gate cannot check whether the work
   is *right*, only whether the report is *complete*.

## Known gaps

Stated plainly so nobody assumes otherwise.

- **Writes are not atomic.** Reports are written with `write_text`, so a reader could in
  principle see a half-written file. In practice the writer is a single agent and the
  reader is woken only after `submit` completes, but a `rename()`-based publish would be
  strictly better.
- **The 3-round cap is advice**, enforced only by the orchestrator playbook.
- **No `verify:` sanity check at scope submission.** Docket could warn when the first word
  of a discovered verification command is not resolvable in `sh`. It does not yet.
- **Wake events are not authenticated.** A wake banner telling an agent to review and
  approve something is shaped exactly like a prompt injection, and reports are written by
  small models. A supervisor should confirm the run exists before acting on a wake.
- **`docket status` truncates the verify column** to 20 characters, which can hide a
  meaningful difference between two commands.

## Code map

Everything lives in `skills/docket/bin/docket`, a single stdlib-only script.

| Region | Contents |
| --- | --- |
| frontmatter | `parse`, `render`, `sections`, `is_empty` |
| paths | `root`, `run_dir`, `need_run`, reports, decisions, scopes, and handoffs |
| templates | `TEMPLATES` dict and `template()`, which honours per-project overrides |
| commands | task validation, discovery scope, handoff, report, review, diff, and preflight |
| signalling | `armed`, `ledger_for`, `cmd_arm`, `cmd_disarm`, `events`, `cmd_watch` |
| doctor | `cmd_doctor` |
| playbooks | `references/*.md`, surfaced by `cmd_help` |

Role playbooks live in `skills/docket/references/`. `SKILL.md` routes each role to the
relevant playbook, while `docket help <role>` exposes the same installed files.

## Testing

```bash
tests/test.sh
```

The Python behavioral suite uses a fresh temporary directory per test. It covers task
intent, implementor-owned discovery, atomic collision detection, scope amendments,
handoffs, report gates, model history, review, topology, and signalling isolation.

**Add a test whenever you fix a bug.** The planner-wake bug shipped precisely because the
planner signal path had never been exercised - the suite now asserts both directions and
that an unarmed role is *not* woken.
