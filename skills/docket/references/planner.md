# Planner / reviewer

The planner owns the plan and the final outcome review. It does not implement code.

## Choose the topology

- `split`: the planner and orchestrator are separate agents. Use this when the
  planner runs on an expensive Claude Code or Codex model and implementation and
  supervision can run more cheaply in OpenCode.
- `combined`: one agent performs both planner/reviewer and orchestrator duties.
  Implementors may still be separate agents. Use the orchestrator workflow after
  the plan is approved; no redundant planner handoff occurs.

Record the choice with `docket init <run> --topology split|combined`.

## Plan

Fill `plan.mdx`, including objective, approach, risks, and every task. Complexity
and ownership are independent:

- `complexity=low` requires a narrow, mechanically checkable task with a runnable
  verification command.
- `complexity=high` requires a stronger model and deeper review.
- `executor=implementor` delegates the task regardless of complexity.
- `executor=orchestrator` keeps the task with the orchestrator.

Define functional intent and constraints, not detailed repository maps. The cheap
implementor owns discovery and proposes its change surface after dispatch.

Approve the plan only after the human agrees to it.

## Split-mode information boundary

Hand the approved plan to the orchestrator. Then stop supervising task work.

The planner must not monitor implementor lifecycle, read `Txx-report` files,
receive task submission wakes, or relay per-task implementation progress to the
human. Those are orchestrator responsibilities. This boundary is intentional: it
protects the expensive planner's context and token budget.

Arm only the planner role and set `DOCKET_ROLE=planner` in its session:

```bash
docket arm <run> --role planner
docket status <run> --role planner
```

The only implementation artifact the planner consumes is the standardized
`orch-report-NN.mdx`. Read that report and the aggregate diff, then approve or
request changes. Never infer task progress from panes or agent lifecycle state.

## Combined mode

Continue with the orchestrator playbook in the same agent. Do not arm the planner
role. The final orchestrator report is still required as the durable summary, but
submission completes it without waking or handing it back to the same agent.

Combining roles does not relax the token boundary. Do not narrate implementor
activity merely because the user-facing planner can now observe it. Follow the
orchestrator's quiet-supervision contract exactly.
