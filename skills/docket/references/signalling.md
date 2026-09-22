# Signalling

Lifecycle state is never proof of task completion. Submitted reports are the
review handoff; ready partial-work checkpoints are resumable context only.

For short work, a harness-specific blocking wait is acceptable. For longer work,
use a role-scoped watcher so the supervisor can idle without polling.

```bash
docket arm <run> --role orchestrator
docket arm <run> --role planner       # split topology only
docket arm <run> --role verifier      # five-role runs: submissions route promptly
docket arm <run> --role reviewer      # five-role runs: milestone batches and frontiers
```

Every watching session must set `DOCKET_ROLE` to the one role it performs. The
native hook (`hooks/wake.sh`) routes planner, orchestrator, verifier, and
reviewer through the same foreground watcher; it is intentionally inert when the
variable is absent or names another role. This prevents a verifier submission
from waking or acting as reviewer, and keeps every role's registration and
ledger separate.

```bash
export DOCKET_ROLE=orchestrator        # or planner, verifier, reviewer
docket watch --armed --role "$DOCKET_ROLE"
```

The CLI claims events under a file lock. Planner events contain only orchestrator
report submissions in legacy runs. Ordinary implementor submissions are batched: the orchestrator
wakes only when every currently delegated task is submitted or already decided.
A revised report produces a new batch signature and one new wake. Blockers,
ready partial-work checkpoints, and genuine discovery-scope collisions remain
immediate. Successful scope claims remain silent. Combined topology uses only the
orchestrator role. In five-role runs, submissions route promptly and individually
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
`herdr agent read` to manufacture progress updates. Use one blocking watch/wait,
then inspect once when it returns. A healthy running state is not user-facing news.

Never background `docket watch` from a Claude Code Stop hook. The watcher must
remain in the hook's foreground process tree so session teardown stops it.
