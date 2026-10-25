# Existing run policies

Use this reference only when the run records an older or custom policy.
New coding work uses quick; `--mode standard` emits a deprecation warning and remains available for compatibility.
Never rewrite a run's mode, workflow, topology, review policy, or round context to adopt new defaults.
The lifecycle code and recorded authority remain unchanged.

## Standard preset window layout

The standard preset builds two herdr windows once, at session setup.
Build the planner window first, from the planner pane, as a 1:1 vertical split with planner and reviewer.
Split the planner pane 1:1 to the right for the reviewer and keep the new pane id from `result.pane.pane_id`.
When `launch:reviewer` arrives, start the reviewer in that prepared pane.
Then create the tab that will hold the orchestrator window.

```bash
herdr pane split --current --direction right --ratio 0.5 --cwd "$PWD" --env DOCKET_ROLE=reviewer --no-focus   # JSON: result.pane.pane_id
herdr tab create --cwd "$PWD" --env DOCKET_ROLE=orchestrator --no-focus   # JSON: result.root_pane.pane_id
# On launch:reviewer, use the reviewer pane id saved above.
herdr agent start reviewer --kind opencode --pane <pane_id> -- --auto
herdr agent start orchestrator --kind codex --pane <root_pane_id> -- --yolo   # or the harness the plan names
```

Start the orchestrator in that tab's root pane.
The tab's `--env DOCKET_ROLE=orchestrator` is what lets the orchestrator's Stop hook fire; a pane without it cannot wait at zero cost.

The orchestrator builds its own window inside that tab: it splits the orchestrator pane 1:1 to the right for the implementor pane and down in half for the verifier, and starts the verifier there.
Only the planner starts the reviewer in standard.
The launch event appears when review work is pending and no reviewer harness session has joined this run.
Only the orchestrator starts the verifier and each implementor.

```bash
herdr pane split --current --direction right --ratio 0.5 --cwd "$PWD" --no-focus   # JSON: result.pane.pane_id
herdr pane split --current --direction down --ratio 0.5 --cwd "$PWD" --env DOCKET_ROLE=verifier --no-focus   # JSON: result.pane.pane_id
herdr agent start verifier --kind opencode --pane <pane_id> -- --auto
```


## Recorded role boundaries

A standard preset run has separate planner, orchestrator, implementor, verifier, and reviewer sessions.
`tiered-verifier-reviewer` settles each passing task for sequencing, but only the reviewer approves its milestone against the frozen full-suite pass.
`independent-verifier-reviewer` keeps per-task review and its recorded verifier-correction setting.
An uncertain verification derives `verification-uncertain` with the exact findings and bundle; uncertainty holds dependencies, scope, and capacity.
A `verification-failed` event needs reviewer changes; failed work never becomes approval-ready.
An orchestrator-owned blocker or blocked aggregate reaches the reviewer directly; an implementor blocker needs `docket route --kind blocked` first.
A closed batch waits for every submitted member's current pass, uncertain, or explicit exemption; fail or correction keeps it unready.
A frontier is only a wake for its exact members, never a combined verdict.
The planner starts only the reviewer; the orchestrator starts verifier and implementors.
Use `docket help orchestrator` and `docket help verifier` for their recorded duties.
Existing `combined-checker` quick runs keep the coordinator and checker, including separately recorded verification and review duties.
Read `coordinator.md` and `checker.md` for those roles.

## Recorded round context

Under `round_context: fresh` on Claude Code or OpenCode, the quick implementor coordinates and hands each bound task prompt to one subagent on its own model.
Dispatch prints the exact instruction; the coordinator does not read the task prompt or implement in its own session.
Keep that subagent across corrections; Claude Code continues with SendMessage to its agent id and OpenCode with the task tool and its task_id.
The subagent stops at submission; the parent runs the milestone suite and waits.
A fresh reviewer similarly keeps one subagent per milestone through corrections and re-reviews; its parent records pickup and waits.
A fresh verifier keeps one subagent per task.
Start a replacement only when the original is gone, using the bound prompt and running correction checklist or correction packet.
A standard worker keeps its task context across corrections and starts a new one for another task.
Codex roles, runs recording `shared`, and runs without the field keep working in their session.

## Combined topology (legacy and custom runs)

Continue with the orchestrator playbook in the same agent.
Do not arm the planner role.
The final orchestrator report is still required as the durable summary, but legacy submission completes it without waking or handing it back to the same agent.
Under `five-role-v1` the aggregate records `submitted` and becomes terminal only through a reviewer decision.

Combining roles does not relax the token boundary.
Do not narrate implementor activity merely because the user-facing planner can now observe it.
Follow the orchestrator's quiet-supervision contract exactly.

In legacy split runs only, the planner reads `orch-report-NN.mdx` and the aggregate diff and casts the final verdict.
In five-role runs it casts no verdict; the reviewer owns approval, waiver, reopen, and changes.
An unchanged legacy aggregate submission can finish without waking the same combined agent.
The standard aggregate verdict archives usage automatically; quick uses `docket usage <run> --archive` after completion.
Use [recovery.md](recovery.md) for interrupted decisions and stale evidence; these rules apply to old policies too.
