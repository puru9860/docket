# Signalling

Lifecycle state is never proof of task completion. Submitted reports are the
review handoff; ready partial-work checkpoints are resumable context only.

For short work, a harness-specific blocking wait is acceptable. For longer work,
use a role-scoped watcher so the supervisor can idle without polling.

```bash
docket arm <run> --role orchestrator
docket arm <run> --role planner       # split topology only
```

Every watching session must set `DOCKET_ROLE` to the one role it performs. The
hook is intentionally inert when the variable is absent; this prevents a planner
from consuming orchestrator events or vice versa.

```bash
export DOCKET_ROLE=orchestrator        # or planner
docket watch --armed --role "$DOCKET_ROLE"
```

The CLI claims events under a file lock. Planner events contain only orchestrator
report submissions. Orchestrator events contain only delegated implementor task
submissions, blockers, and ready partial-work checkpoints. Combined topology uses
only the orchestrator role. A genuine discovery-scope collision is also actionable;
successful scope claims remain silent. Checkpoint readiness wakes the orchestrator so it can
replace an implementor without polling, but is not forwarded to a split planner
or narrated to the user.

Waiting is silent. Do not repeatedly call `docket status`, `herdr agent get`, or
`herdr agent read` to manufacture progress updates. Use one blocking watch/wait,
then inspect once when it returns. A healthy running state is not user-facing news.

Never background `docket watch` from a Claude Code Stop hook. The watcher must
remain in the hook's foreground process tree so session teardown stops it.
