# Signalling

Lifecycle state is never proof of task completion. Submitted reports are the
review handoff; ready partial-work checkpoints are resumable context only.

Use a role-scoped watcher so each waiting role can idle without polling. In new
quick runs this includes the implementor while a milestone is under review.

## Waiting without burning tokens

A wait costs tokens only when the harness re-enters the model, and every re-entry replays the session's whole context.
Wait on Docket events, never on agent state: `herdr agent wait`, `sleep` loops, and repeated `docket status` calls return on idle transitions and timers rather than on the lifecycle change that needs you.
One `docket watch <run> --role "$DOCKET_ROLE"` returns exactly when something is actionable for that role.

| Harness | How to wait |
| --- | --- |
| Claude Code | Best: the Stop hook with `asyncRewake` below, which waits at zero token cost. Otherwise run `docket watch` as one background command (`run_in_background`) without a short `--timeout`; the model is re-entered only when it exits. A foreground call is capped at 10 minutes by the tool. |
| Codex | Best: the Stop hook in `hooks/codex-hooks.json.example`, merged into `~/.codex/hooks.json` and trusted once in `/hooks`. When your turn ends it waits at no model cost and hands you the wake as your next prompt, so end your turn instead of starting a watcher. Without it: start `docket watch` once and keep waiting on that same command: pass the `yield_time_ms` that `docket watch` prints when it starts to every `write_stdin` and `wait` poll. With `background_terminal_max_timeout = 3600000` at the top level of `~/.codex/config.toml` that is 3600000: one poll then idles for up to an hour at no model cost and returns the moment the wake lands. Codex re-enters the model on every return, and its first yields come back after about 30 seconds whatever you pass, so only the long window keeps a wait to a few turns. |
| OpenCode | Run `docket watch <run> --role R --timeout 590` only in a terminal or adapter whose shell call can last at least 600 seconds. OpenCode `run` mode can cap a shell call at 120 seconds, which cannot host this foreground watch. Use a longer-lived terminal or delivery adapter for unattended supervision; a short retry loop burns a model turn on every return. |

A planner can wake an armed supervisor with `docket instruct <run> --role
reviewer --message TEXT`. The role handles the instruction and closes it with
`docket instruct <run> --role reviewer --resolve <ID>`. The watcher returns
as soon as the instruction event is derived. If text typed into a Codex pane
waits behind a blocking tool call, send Escape to that pane, for example
`herdr agent send-keys <agent> Escape`, so Codex can process the queued text.

The watcher exits 0 silently when its timeout passes with nothing actionable; re-arm the same wait without narrating it.

Inside a harness, `docket watch` refuses a `--timeout` under 590 seconds: a shorter window only returns to the model to start the same wait again.
The hook is exempt, since its waits cost nothing.
Under Codex the watcher also says whether the installed docket Stop hook serves this session or is inert because the session's own `DOCKET_ROLE` is missing or names another role.
A session gets that variable only from the pane or tab that started it (`--env DOCKET_ROLE=<role>`), never from a prefix on one command.

`docket status` names the same waste from the other side.
When the calling session reads a status identical to the one it read last, the output says so and how many times it has been read unchanged.
When a Codex session plays or is assigned any role, `docket status` also shows the account's Codex 5-hour and weekly usage, read from the newest local rollouts, and warns at 70% of the 5-hour window or 90% of the weekly one.
Relay that warning to the user once: a Codex role stops mid-task when a window runs out, and moving roles to another harness is the user's call.

Arm every role that waits. A role that is not armed is never woken by the hook.

```bash
docket arm <run> --role planner       # new quick and standard: objective and completion
docket arm <run> --role implementor   # new quick: execution and milestone suite runs
docket arm <run> --role reviewer      # new quick: milestone decisions
docket arm <run> --role coordinator   # old combined-checker quick runs
docket arm <run> --role checker       # old combined-checker quick runs
docket arm <run> --role orchestrator  # standard
docket arm <run> --role verifier      # standard: submissions route promptly
```

Every watching session must set `DOCKET_ROLE` to the one role it performs. The
native hook (`hooks/wake.sh`) routes coordinator, checker, planner, orchestrator,
implementor, verifier, and reviewer through the same foreground watcher; it is intentionally inert when the
variable is absent or names another role. This prevents a verifier submission
from waking or acting as reviewer, and keeps every role's registration and
ledger separate.

```bash
export DOCKET_ROLE=checker             # or coordinator, planner, orchestrator, verifier, reviewer
docket watch --armed --role "$DOCKET_ROLE"
```

The CLI claims events under a file lock. Planner events contain only orchestrator
report submissions in legacy runs. Ordinary implementor submissions are batched: the orchestrator
wakes only when every currently delegated task is submitted or already decided.
A revised report produces a new batch signature and one new wake. Blockers,
ready partial-work checkpoints, and genuine discovery-scope collisions remain
immediate. Successful scope claims remain silent. A quick run uses the coordinator and
checker roles; a legacy combined run uses only the orchestrator role. In five-role runs, submissions route promptly and individually
to the verifier, while capable-review readiness is emitted only at configured
milestone batches for the reviewer. A closed batch whose verified submission
blocks a dependent derives one reviewer frontier for exactly those members,
suppressed when milestone readiness already covers the same evidence and never
derived for legacy runs. A frontier key carries the batch and generation while
its revision carries the verification shape, so unchanged frontiers stay stable
and a changed verdict, finding, round, or second bundle moves the revision.
A submitted or blocked aggregate wakes the
reviewer, never the planner; the planner keeps intent and constraint amendments
only. A recorded verifier verdict for the exact round and evidence resolves its
verifier event, and a new round or a re-review with a second bundle derives a
new event. A recorded `fail` verdict routes to someone who can act without ever
making failed work look reviewable: the reviewer receives one
`verification-failed` event per failed submitted round, the legacy whole-run
review batch no longer claims readiness for it, and milestone readiness and
review frontiers still exclude it exactly as before. The failure event resolves
when the failure is answered, by a correction round opening or by a reviewer
decision, and never re-wakes a role that already handled it. When a verifier
opens a correction under `verifier_correction: allowed`, the orchestrator
receives one `correction-ready` event per pending correction round so it can
re-dispatch the implementor. A milestone batch is ready only when every submitted member has a
resolved `pass` or `uncertain` verification or exempt coverage.

Under `five-role-v1` every review readiness wake means verified, not merely submitted.
The whole-run review batch and every closed batch, milestone or not, wait until each submitted member holds a resolved verification, and the verdicts and findings are part of the event identity.
A supervisor woken at submission would have nothing to route and no later event to wait for, so it would poll.
In tiered standard runs, settled batch members wake the orchestrator with
`batch:<id>:verify-ready` to start the full suite. A running suite suppresses
that event. A current failure wakes the orchestrator for recovery; a current
pass wakes only the reviewer for milestone review. An uncertain verifier verdict
wakes the reviewer immediately with its findings and frozen bundle identity,
without releasing dependent work. Earlier independent-policy standard runs
keep their review relay through the orchestrator.
In a new quick run the reviewer wakes once a whole milestone has its full-suite pass.
The implementor then waits for a milestone approval event before dispatching the
next milestone, or for a correction event that needs its action.
The quick implementor alone owns `launch:reviewer`; standard keeps that launch
with the planner. Use the registered inbox claim before launching, so competing
sessions of the same role cannot hold the launch lease together.
Old `combined-checker` quick runs route ready work to the checker instead.
An orchestrator-owned task counts toward review readiness and the all-decided wake like delegated work, because five-role submission is never terminal for it either.

Every correction wakes whoever must start it, and only until it starts.
A `correction-ready` event retires as soon as a live dispatch record binds that correction round, so a dispatched implementor is never re-announced.
A correction on an orchestrator-owned task, and a reviewer or planner changes decision on the aggregate, wake the orchestrator the same way.
A blocked orchestrator-owned task wakes the reviewer directly, as a blocked aggregate does, since the orchestrator already knows it blocked.

An interrupted decision is never silent.
A decision transition left unfinished, whether it stopped after its journal, its decision artifact, or its report write, derives one `unfinished-<verdict>` event naming the exact command that finishes it.
It goes to the reviewer in five-role runs, including for a verifier-opened correction, and to the legacy orchestrator or split planner that decides that owner otherwise.
A verifier correction interrupted before its transition began wakes the reviewer as a failed verification whose correction did not finish.
A transition whose owner lock is held is still running and derives nothing.

After a native hook wakes you, record receipt before acting:

```bash
docket pickup <run> --role "$DOCKET_ROLE"
```

Pickup claims and acknowledges the current announced events in one operation,
bound to their revision and this recipient's registered session generation.
Printing or forwarding a banner never records native receipt. If the hook dies,
forwarding fails, or the session never picks up the announcement, it retries after
the announcement lease. A received wake stays quiet while its identity holds.
Manual watchers retain their output-based delivery contract.
An event usually stays derived after delivery because another role is still working on it, and re-announcing it every lease period would be polling by another name, so it is not re-announced.
An announcement that crashed before delivery is re-announced once its lease expires, and an event whose round, revision, role, workflow, or generation moves is retired and re-derived as a new wake.

Arm each waiting role from its own harness session. Arming registers its run,
role, and process generation; a restarted session must arm again. The native hook
selects that recipient's runs. Set `DOCKET_RUN=R01` to narrow the binding or use
an explicit comma-separated list for multi-run supervision. If harness detection
is unavailable, `docket arm R01 --role reviewer --session reviewer-1` plus a
session started with `DOCKET_SESSION=reviewer-1` uses the existing transport
registry. Re-register that explicit ID when restarting. A terminal hook with no
identity can serve one armed run, but refuses ambiguous same-role runs.
The hook finds the nearest `.docket` ancestor when started in a subdirectory.

`doctor` and the Codex wait hint share one detector. It accepts the Docket adapter,
checks synchronous Codex behavior, honors `CODEX_HOME`, and reads `hooks.json` and
inline `config.toml` hooks. Local configuration and enablement are evidence only:
effective overrides and current trust remain unknown until confirmed in `/hooks`
and a real wake. An async Codex handler cannot continue an idle turn.

The native hook's wait expires after approximately eight idle hours. It then
exits 0 without a continuation, so an already idle session is not automatically
re-armed. Use external session renewal for longer unattended waits. Docket does
not add timer-driven model wakeups to hide that harness limit. New session notes
retain proven process identity so a dead supervisor can request a replacement
launch after the exit grace period; old notes without that evidence stay unknown.

`docket events <run> --role "$DOCKET_ROLE" --peek` inspects the same events without
claiming them, so nothing is consumed and no ledger is created or migrated. Use it
to see what is pending; use `docket watch` to receive it. For durable,
crash-recoverable handling, reconcile first and then claim explicitly:

```bash
docket reconcile <run>
docket session <run> --register --session SESSION --name NAME --role "$DOCKET_ROLE"
docket inbox <run> --role "$DOCKET_ROLE" --claim --session SESSION
docket events <run> --role "$DOCKET_ROLE" --ack EVENT --session SESSION
```

A claim holds one bounded lease; acknowledgement is receipt, never completion,
and a durable decision or resolution retires the event automatically. Delivery is
at-least-once: a retry releases the lease with a recorded reason, an expired lease
becomes claimable again, and a stale event (moved round, revision, role, workflow,
or generation) is retired instead of acted on. Re-register the session whenever it
restarts; the old generation can neither claim nor acknowledge afterwards.

`docket reconcile` also reconciles dispatch records against the lifecycle
documents. A record whose round, owner, or session no longer describes live
work is released and named as `reconciled dispatch OWNER: reason`, with the
reason derived from the task, report, and registration documents rather than
from the record itself. Execution capacity shown by `docket status` and
`docket health` is the same live count, so a released slot is visible at once
while accepted scope stays held.

A normal review-batch wake is internal work. Review, decide, and wait again
without sending lifecycle or per-task progress to the user. Checkpoint readiness
wakes the orchestrator only so it can replace an implementor; it is not narrated.

Waiting is silent. Do not repeatedly call `docket status`, `herdr agent get`, or
`herdr agent read` to manufacture progress updates. Use one blocking `docket watch`,
then inspect once when it returns. A healthy running state is not user-facing news.

Never background `docket watch` from a Claude Code Stop hook. The watcher must
remain in the hook's foreground process tree so session teardown stops it.
