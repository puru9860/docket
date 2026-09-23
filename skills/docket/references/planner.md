# Planner

The planner owns the plan and its intent and constraint amendments. It does not implement code, verify evidence, or review task rounds: approval, waiver, reopen, and changes verdicts belong to the reviewer working from a fresh context.

## Choose the topology

- `split`: the planner and orchestrator are separate agents. Use this when the
  planner runs on an expensive Claude Code or Codex model and implementation and
  supervision can run more cheaply in OpenCode.
- `combined`: one agent performs both planner/reviewer and orchestrator duties.
  Implementors may still be separate agents. Use the orchestrator workflow after
  the plan is approved; no redundant planner handoff occurs.

Record the choice with `docket init <run> --topology split|combined`.

## Choose the preset

New runs start on the quick preset unless `--mode standard` selects the five-session one.
The standard preset keeps planner and orchestrator as separate sessions with an independent verifier behind every decision.
The quick preset merges planning and orchestration into one coordinator session for work with one clear outcome, established local verification, one writer, and no unresolved requirement or architecture decision.
A coordinator owns exactly the planner duties described here plus deterministic dispatch.
Nothing about intent ownership or amendment authority is weakened by the merge.
A run that outgrows quick records an escalation request instead of migrating silently.

## Choose the review evidence

`docket init` records `evidence_mode: git` when the workspace is a checkout and
`evidence_mode: documents-only` when it is not; `--evidence-mode` overrides it.

A `git` run declares its checkout roots explicitly. Name each one as
`--root <alias>=<path>` at `docket init`, or with `docket roots <run> --declare`
before the first assignment. Omitting them declares only the enclosing checkout, so
a linked worktree, a nested repository, or a sibling checkout under a non-Git parent
has to be named or it is never captured. Create the checkouts the run needs before
declaring, because the declaration cannot be changed once a baseline points at it.
A `git` run that cannot capture a complete baseline refuses to dispatch rather than
recording a partial one. A documents-only run stays fully usable and labels its diff
coverage unavailable rather than pretending the tree is unchanged.

Either way, every submitted round is frozen into an immutable evidence bundle and
every verdict binds to it, so a review is always a review of something that cannot
change afterwards.

## Choose the integration policy

`plan.mdx` carries `provisional_integration: forbidden` by default: a task may only
consume another task's evidence once that task is approved. Declare
`provisional_integration: allowed` when the plan genuinely needs one task to build on
another's verified but unapproved work. Read that word literally: the policy admits only
a round whose frozen verification passed, so a blocked or skipped task is never a
dependency, however urgent the sequencing. It pins the exact evidence consumed and forces
reverification if it moves, withdrawing the consumer's review readiness until then. That
reverification costs a full correction round: the consumed pin is frozen into the round,
so restoring readiness means a changes verdict, a re-recorded input, and another
submission, not a quiet refresh. It never turns readiness into approval, and it never
removes the independent review of either task, so prefer sequencing the tasks when
sequencing is affordable.

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
`orch-report-NN.mdx` in legacy split runs. In `five-role-v1` runs the aggregate
wakes the reviewer instead, and the planner receives intent and constraint
amendments only. In legacy split runs, read that report and the aggregate diff,
then approve or request changes; in `five-role-v1` runs cast no verdict, because
approval, waiver, reopen, and changes verdicts belong to the reviewer. Never
infer task progress from panes or agent lifecycle state.

In legacy split runs, if a decision is interrupted, repeat the verdict alone: Docket recorded your reason
and reviewer identity before writing anything, so the retry finishes that decision
with your wording intact, and `docket status` lists any transition still unfinished.
A retry that states anything the transition did not record is refused rather than
allowed to redefine the decision, including one that supplies a reason where the
interrupted attempt recorded none. If Docket says the report changed after review
began, re-read it and either ask for the reviewed body back or re-decide with
`--re-review`, which puts the new body back through the whole report gate and then
re-verifies it; a blocked aggregate report clears the blocked checks and skips
completion verification there, exactly as it does at submission.

## Combined mode

Continue with the orchestrator playbook in the same agent. Do not arm the planner
role. The final orchestrator report is still required as the durable summary, but
legacy submission completes it without waking or handing it back to the same
agent. Under `five-role-v1` the aggregate records `submitted` and becomes
terminal only through a reviewer decision.

Combining roles does not relax the token boundary. Do not narrate implementor
activity merely because the user-facing planner can now observe it. Follow the
orchestrator's quiet-supervision contract exactly.

## Prompt rendering

A planner prompt about the aggregate carries the plan objective, approach,
risks, and hard constraints with the plan revision, never task placeholders
such as a goal of `see task file`. Prompts render for a workflow, role, and
stage from the canonical contracts; the stage is derived from lifecycle
documents and an explicit flag is for inspection only. Mandatory contract and
acceptance material is never truncated to fit a budget. Optional guidance
lives under a token budget that covers the profile, every card, and their
headers and separators; zero selects nothing and an oversized first card is
skipped. Model matching is exact on the normalized identifier and one-run
model defaults are removed; an unknown model receives only task-relevant
cards. Literal commands, fences, and tables survive unchanged.
